from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from scipy.interpolate import interp1d

from film_tracks_aligner.models import Track, WarpMap


def adjust_subtitle(
    track: Track,
    subtitle_path: Path,
    warp_map: WarpMap,
    output_dir: Path,
) -> tuple[Path, list[str], int]:
    """Adjust subtitle timestamps according to the warp map.

    Returns:
        (adjusted_path, warnings, delay_ms): path to the adjusted subtitle file,
        any warnings, and an optional mux-time delay for formats that cannot be
        fully remapped in-file.
    """
    warnings: list[str] = []
    fmt = track.sub_format or ""

    if fmt in ("pgs", "vobsub"):
        delay_ms = _constant_subtitle_delay_ms(warp_map)
        speed_factor = _linear_speed_factor(warp_map)
        warnings.append(
            f"Subtitle format '{fmt}' is bitmap-based. "
            "Only a constant time offset can be applied during muxing. "
            "Fine-grained synchronization is not possible."
        )
        if speed_factor is not None and abs(speed_factor - 1.0) >= 1e-3:
            warnings.append(
                f"Bitmap subtitle timing also needs stretch ({speed_factor:.6f}x), "
                "but only the constant offset can be preserved."
            )
        # For bitmap subs, we can only apply a global offset via mkvmerge --sync
        # Return the original path and let the muxer handle offset-only.
        return subtitle_path, warnings, delay_ms

    # Build time interpolator from warp map
    warp_fn = _build_warp_function(warp_map)

    if fmt == "srt":
        adjusted_path = output_dir / (subtitle_path.stem + "_adjusted.srt")
        _adjust_srt(subtitle_path, adjusted_path, warp_fn)
    elif fmt in ("ass", "ssa"):
        adjusted_path = output_dir / (subtitle_path.stem + "_adjusted.ass")
        _adjust_ass(subtitle_path, adjusted_path, warp_fn)
    else:
        warnings.append(f"Unknown subtitle format '{fmt}'. Copying as-is.")
        adjusted_path = output_dir / subtitle_path.name
        adjusted_path.write_bytes(subtitle_path.read_bytes())

    return adjusted_path, warnings, 0


def _build_warp_function(warp_map: WarpMap):
    """Build a scipy interpolator: t_sel → t_ref (maps selected timeline to reference)."""
    if not warp_map.segments:
        return lambda t: t

    t_sel_points = []
    t_ref_points = []
    for seg in warp_map.segments:
        t_sel_points.extend([seg.t_sel_start, seg.t_sel_end])
        t_ref_points.extend([seg.t_ref_start, seg.t_ref_end])

    t_sel_arr = np.array(t_sel_points)
    t_ref_arr = np.array(t_ref_points)

    fn = interp1d(
        t_sel_arr, t_ref_arr,
        kind="linear",
        bounds_error=False,
        fill_value=(t_ref_arr[0], t_ref_arr[-1]),
    )
    return fn


def _constant_subtitle_delay_ms(warp_map: WarpMap) -> int:
    if not warp_map.segments:
        return 0
    seg = warp_map.segments[0]
    return int(round((seg.t_ref_start - seg.t_sel_start) * 1000))


def _linear_speed_factor(warp_map: WarpMap) -> float | None:
    if not warp_map.is_linear_drift or not warp_map.segments:
        return None

    seg = warp_map.segments[0]
    ref_dur = seg.t_ref_end - seg.t_ref_start
    sel_dur = seg.t_sel_end - seg.t_sel_start
    if ref_dur <= 0:
        return None
    return sel_dur / ref_dur


# ---- SRT parser/writer ----

_SRT_TIMESTAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _srt_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _sec_to_srt(t: float) -> str:
    t = max(0.0, t)
    ms = round((t % 1) * 1000)
    s = int(t) % 60
    m = (int(t) // 60) % 60
    h = int(t) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _adjust_srt(input_path: Path, output_path: Path, warp_fn) -> None:
    text = input_path.read_text(encoding="utf-8", errors="replace")

    def replace_ts(m: re.Match) -> str:
        t_start = _srt_to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        t_end = _srt_to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        new_start = float(warp_fn(t_start))
        new_end = float(warp_fn(t_end))
        return f"{_sec_to_srt(new_start)} --> {_sec_to_srt(new_end)}"

    adjusted = _SRT_TIMESTAMP.sub(replace_ts, text)
    output_path.write_text(adjusted, encoding="utf-8")


# ---- ASS/SSA parser/writer ----

_ASS_TIMESTAMP = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")


def _ass_to_sec(h: str, m: str, s: str, cs: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def _sec_to_ass(t: float) -> str:
    t = max(0.0, t)
    cs = round((t % 1) * 100)
    s = int(t) % 60
    m = (int(t) // 60) % 60
    h = int(t) // 3600
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _adjust_ass(input_path: Path, output_path: Path, warp_fn) -> None:
    lines = input_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    out_lines: list[str] = []

    for line in lines:
        # Dialogue and Comment lines have timestamps at columns 2 and 3 (0-indexed)
        stripped = line.strip()
        if stripped.startswith(("Dialogue:", "Comment:")):
            parts = line.split(",", 9)
            if len(parts) >= 3:
                # parts[1] = start time, parts[2] = end time
                def replace_in_part(part: str) -> str:
                    def repl(m: re.Match) -> str:
                        t = _ass_to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
                        return _sec_to_ass(float(warp_fn(t)))
                    return _ASS_TIMESTAMP.sub(repl, part)

                parts[1] = replace_in_part(parts[1])
                parts[2] = replace_in_part(parts[2])
                out_lines.append(",".join(parts))
                continue
        out_lines.append(line)

    output_path.write_text("".join(out_lines), encoding="utf-8")
