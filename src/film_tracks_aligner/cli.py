from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel

from film_tracks_aligner.mkv.extractor import (
    extract_audio_as_wav,
    extract_subtitle,
)
from film_tracks_aligner.mkv.muxer import mux_output
from film_tracks_aligner.models import TrackSelection, WarpMap
from film_tracks_aligner.subtitle.adjuster import adjust_subtitle
from film_tracks_aligner.sync.aligner import MIN_CONFIDENCE, compute_warp_map
from film_tracks_aligner.sync.analyzer import extract_features
from film_tracks_aligner.sync.applier import apply_warp
from film_tracks_aligner.tui.app import FilmAlignerApp

app = typer.Typer(
    name="frankensync",
    help="Merge video and audio tracks from MKV files with automatic audio synchronization.",
    add_completion=False,
)
console = Console()


@app.command()
def main(
    file1: Annotated[
        Path | None,
        typer.Argument(help="First MKV file (optional when running inside a directory of MKVs)"),
    ] = None,
    file2: Annotated[
        Path | None,
        typer.Argument(help="Second MKV file (optional when running inside a directory of MKVs)"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output MKV path"),
    ] = None,
) -> None:
    input_files, discovered_mode = _resolve_input_files(file1, file2)
    _validate_inputs(input_files)

    # --- Step 1: TUI track selection ---
    tui = FilmAlignerApp(files=input_files, output_path=output)
    selection: TrackSelection | None = tui.run()

    if selection is None:
        console.print(
            Panel(
                "[bold]No selection made.[/bold]\nSee you next time!",
                title="frankensync",
                border_style="yellow",
                expand=False,
            )
        )
        raise typer.Exit(0)

    if output is None:
        if discovered_mode:
            output = Path.cwd() / f"{selection.video.file_path.stem}_merged.mkv"
        else:
            output = Path.cwd() / f"{input_files[0].stem}_merged.mkv"

    console.print()
    console.print("[bold green]Selection complete![/bold green]")
    console.print(f"  Video   : {escape(selection.video.display_label)}  [dim]({escape(selection.video.file_path.name)})[/dim]")
    console.print(f"  Audio   : {escape(selection.audio.display_label)}  [dim]({escape(selection.audio.file_path.name)})[/dim]")
    if selection.subtitle:
        console.print(
            f"  Subtitle: {escape(selection.subtitle.display_label)}  [dim]({escape(selection.subtitle.file_path.name)})[/dim]"
        )
    else:
        console.print("  Subtitle: [dim]none[/dim]")
    console.print(f"  Output  : [cyan]{escape(str(output))}[/cyan]")
    console.print()

    # --- Step 2: Processing pipeline ---
    with tempfile.TemporaryDirectory(prefix="frankensync-") as tmpdir:
        tmp = Path(tmpdir)
        _run_pipeline(selection, tmp, output)

    console.print(f"\n[bold green]Done![/bold green] Output saved to: [cyan]{escape(str(output))}[/cyan]")


def _run_pipeline(selection: TrackSelection, tmp: Path, output: Path) -> None:
    same_source = selection.audio.file_path == selection.video.file_path
    ref_same_as_sel = selection.reference_audio.stream_index == selection.audio.stream_index

    warp_map: WarpMap
    warped_audio: tuple[Path, int] | None = None
    adjusted_subtitle_path: Path | None = None
    subtitle_delay_ms = 0

    if same_source and ref_same_as_sel:
        # Selected audio IS the reference — no sync needed
        console.print("[cyan]Audio and video are from the same source — no sync analysis needed.[/cyan]")
        warp_map = WarpMap(
            segments=[],
            ref_duration=0.0,
            sel_duration=0.0,
        )
    else:
        # --- Extract analysis WAVs ---
        console.print("[cyan]Extracting reference audio for analysis…[/cyan]")
        ref_wav = tmp / "reference.wav"
        extract_audio_as_wav(selection.reference_audio, ref_wav)

        console.print("[cyan]Extracting selected audio for analysis…[/cyan]")
        sel_wav = tmp / "selected.wav"
        extract_audio_as_wav(selection.audio, sel_wav)

        # --- Feature extraction ---
        console.print("[cyan]Extracting audio features…[/cyan]")
        chroma_ref, onset_ref, ref_dur = extract_features(ref_wav, progress_cb=_log)
        chroma_sel, onset_sel, sel_dur = extract_features(sel_wav, progress_cb=_log)

        # --- Alignment with live display ---
        display = AlignmentDisplay(console)
        with display:
            warp_map, confidence = compute_warp_map(
                chroma_ref, chroma_sel,
                onset_ref, onset_sel,
                ref_dur, sel_dur,
                progress_cb=display.on_progress,
                on_chunk=display.on_chunk,
            )

        if warp_map.is_linear_drift:
            if warp_map.segments:
                seg = warp_map.segments[0]
                seg_ref = seg.t_ref_end - seg.t_ref_start
                seg_sel = seg.t_sel_end - seg.t_sel_start
                factor = (seg_sel / seg_ref) if seg_ref > 0 else 1.0
                offset = seg.t_sel_start - seg.t_ref_start
                warped_audio = apply_warp(
                    selection.audio,
                    selection.reference_audio,
                    warp_map,
                    tmp,
                    progress_cb=_log,
                )
                console.print(_format_confidence_message(confidence, factor, offset, has_cuts=False))
                if warped_audio is None:
                    console.print(
                        f"[cyan]Linear drift detected (speed factor {factor:.6f}, "
                        f"offset {offset:+.2f}s). Using lossless mkvmerge --sync.[/cyan]"
                    )
                else:
                    console.print(
                        f"[cyan]Linear drift detected (speed factor {factor:.6f}, "
                        f"offset {offset:+.2f}s). Trimming unmatched edges with ffmpeg.[/cyan]"
                    )
            else:
                console.print(_format_confidence_message(confidence, 1.0, 0.0, has_cuts=False))
                console.print("[cyan]No correction needed.[/cyan]")
        else:
            console.print(_format_confidence_message(confidence, None, None, has_cuts=True))
            console.print(
                f"[cyan]Cuts detected ({len(warp_map.segments)} segments). "
                "Re-encoding audio with ffmpeg.[/cyan]"
            )
            warped_audio = apply_warp(
                selection.audio,
                selection.reference_audio,
                warp_map,
                tmp,
                progress_cb=_log,
            )

    # --- Subtitle adjustment ---
    if selection.subtitle is not None:
        sub_path = extract_subtitle(selection.subtitle, tmp)
        sub_from_video_file = (
            selection.subtitle.file_path == selection.video.file_path
        )
        if sub_from_video_file:
            # Subtitle is already in the reference (video) timeline — no remapping needed.
            console.print(
                "[cyan]Subtitle is from the video file — already in sync, copying as-is.[/cyan]"
            )
            adjusted_subtitle_path = sub_path
            sub_warnings: list[str] = []
        else:
            # Subtitle is from the audio file — its timestamps must be remapped
            # using the same warp map computed for the audio track.
            console.print("[cyan]Extracting and adjusting subtitles…[/cyan]")
            adjusted_subtitle_path, sub_warnings, subtitle_delay_ms = adjust_subtitle(
                selection.subtitle, sub_path, warp_map, tmp
            )
        for w in sub_warnings:
            console.print(f"[yellow]Subtitle warning: {w}[/yellow]")

    # --- Mux ---
    console.print("[cyan]Muxing output with mkvmerge…[/cyan]")
    mux_output(
        selection=selection,
        warped_audio=warped_audio,
        adjusted_subtitle_path=adjusted_subtitle_path,
        subtitle_delay_ms=subtitle_delay_ms,
        warp_map=warp_map,
        output_path=output,
    )


def _log(message: str) -> None:
    console.print(f"  [dim]{message}[/dim]")


def _format_confidence_message(
    confidence: float,
    factor: float | None,
    offset: float | None,
    *,
    has_cuts: bool,
) -> str:
    if confidence >= MIN_CONFIDENCE:
        return f"[green]Sync confidence: {confidence:.2f}[/green]"

    if not has_cuts and factor is not None and offset is not None:
        if abs(factor - 1.0) < 1e-3 and abs(offset) < 0.25:
            return (
                f"[yellow]Weak feature match ({confidence:.2f}), but the alignment "
                "resolved to essentially no correction.[/yellow]"
            )
        return (
            f"[yellow]Weak feature match ({confidence:.2f}). The computed timing "
            "adjustment is still small/simple, but it is worth spot-checking.[/yellow]"
        )

    return (
        f"[yellow]Weak feature match ({confidence:.2f}). The tracks may differ too "
        "much for fully reliable cut detection, so spot-check the result.[/yellow]"
    )


# ---------------------------------------------------------------------------
# Live alignment display
# ---------------------------------------------------------------------------

_SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"  # ▁▂▃▄▅▆▇█
_SPARK_MAX_W = 60
_BAR_W = 30
_CUT_VIZ_SEC = 3.0


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    if len(values) > _SPARK_MAX_W:
        step = len(values) / _SPARK_MAX_W
        values = [values[int(i * step)] for i in range(_SPARK_MAX_W)]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span < 1e-9:
        return _SPARK[3] * len(values)
    out: list[str] = []
    for v in values:
        idx = int((v - lo) / span * (len(_SPARK) - 1))
        out.append(_SPARK[min(idx, len(_SPARK) - 1)])
    return "".join(out)


def _offset_sparkline(offsets: list[float]) -> str:
    if not offsets:
        return ""
    if len(offsets) > _SPARK_MAX_W:
        step = len(offsets) / _SPARK_MAX_W
        offsets = [offsets[int(i * step)] for i in range(_SPARK_MAX_W)]
    lo, hi = min(offsets), max(offsets)
    span = hi - lo
    if span < 1e-9:
        return _SPARK[3] * len(offsets)
    out: list[str] = []
    for i, v in enumerate(offsets):
        if i > 0 and abs(offsets[i] - offsets[i - 1]) > _CUT_VIZ_SEC:
            out.append("[bold red]\u2502[/]")
        idx = int((v - lo) / span * (len(_SPARK) - 1))
        out.append(_SPARK[min(idx, len(_SPARK) - 1)])
    return "".join(out)


class AlignmentDisplay:
    """Rich Live panel showing alignment progress with sparklines."""

    def __init__(self, con: Console) -> None:
        self._console = con
        self._live: Live | None = None
        self._status = ""
        self._anchors: list[tuple[float, float, float]] = []
        self._chunk_idx = 0
        self._n_chunks = 1

    # -- Callbacks for compute_warp_map --

    def on_progress(self, message: str) -> None:
        self._status = message
        self._refresh()

    def on_chunk(
        self,
        chunk_idx: int,
        n_chunks: int,
        anchor: tuple[float, float, float] | None,
    ) -> None:
        self._chunk_idx = chunk_idx + 1
        self._n_chunks = n_chunks
        if anchor is not None:
            self._anchors.append(anchor)
        self._refresh()

    # -- Context manager --

    def __enter__(self) -> "AlignmentDisplay":
        self._live = Live(
            self._render(), console=self._console, refresh_per_second=4,
        )
        self._live.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.stop()
            self._live = None

    # -- Rendering --

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Panel:
        lines: list[str] = []

        if self._status:
            lines.append(f"[dim]{escape(self._status)}[/dim]")
            lines.append("")

        offsets = [t_sel - t_ref for t_ref, t_sel, _ in self._anchors]
        confs = [c for _, _, c in self._anchors]

        if offsets:
            lo, hi = min(offsets), max(offsets)
            lines.append(f"[bold]Offset (s)[/bold]  {lo:+.2f} .. {hi:+.2f}")
            lines.append(f"  [cyan]{_offset_sparkline(offsets)}[/cyan]")
            lines.append("")

        if confs:
            mean_c = sum(confs) / len(confs)
            lines.append(f"[bold]Confidence[/bold]  mean: {mean_c:.2f}")
            lines.append(f"  [green]{_sparkline(confs)}[/green]")
            lines.append("")

        pct = self._chunk_idx * 100 // self._n_chunks if self._n_chunks > 0 else 0
        filled = self._chunk_idx * _BAR_W // self._n_chunks if self._n_chunks > 0 else 0
        bar = "\u2588" * filled + "\u2591" * (_BAR_W - filled)
        lines.append(
            f"Chunks: {self._chunk_idx}/{self._n_chunks}  {bar}  {pct}%"
        )

        return Panel(
            "\n".join(lines),
            title="Alignment",
            border_style="cyan",
            expand=False,
            width=70,
        )


def _resolve_input_files(file1: Path | None, file2: Path | None) -> tuple[list[Path], bool]:
    if file1 is None and file2 is None:
        mkvs = sorted(
            path for path in Path.cwd().iterdir()
            if path.is_file() and path.suffix.lower() == ".mkv"
        )
        if not mkvs:
            console.print("[red]Error: no MKV files found in the current directory.[/red]")
            raise typer.Exit(1)
        return mkvs, True

    if file1 is None or file2 is None:
        console.print("[red]Error: provide either two MKV files or no positional arguments.[/red]")
        raise typer.Exit(1)

    return [file1, file2], False


def _validate_inputs(files: list[Path]) -> None:
    errors: list[str] = []
    for f in files:
        if not f.exists():
            errors.append(f"File not found: {f}")
        elif f.suffix.lower() != ".mkv":
            errors.append(f"Not an MKV file: {f}")
    if errors:
        for e in errors:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
