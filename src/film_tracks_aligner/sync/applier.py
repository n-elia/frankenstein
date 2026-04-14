from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from film_tracks_aligner.models import Track, WarpMap, WarpSegment


# Codecs that ffmpeg can encode (used for re-encode fallback matching)
_ENCODABLE_CODECS: dict[str, str] = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "ac3": "ac3",
    "eac3": "eac3",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}

# Fallback output codec if the source codec cannot be re-encoded
_FALLBACK_CODEC = "aac"
_FALLBACK_BITRATE = "320k"

# Hard bounds on per-segment speed factor. A segment whose sel/ref ratio falls
# outside this range almost certainly comes from a bad anchor (not a real
# audio speed change) and would produce absurd atempo chains that compress or
# stretch audio by orders of magnitude. We skip such segments rather than
# letting ffmpeg destroy the output.
_MAX_SEGMENT_SPEED = 2.0
_MIN_SEGMENT_SPEED = 0.5

# Segments shorter than this (in reference seconds) are skipped as noise.
_MIN_SEGMENT_SEC = 1.0
_EDGE_TOLERANCE_SEC = 0.01


@dataclass(frozen=True)
class AudioPiece:
    source: str  # "selected" | "reference"
    src_start: float
    src_end: float
    ref_start: float
    ref_end: float
    speed_factor: float = 1.0


def apply_warp(
    audio_track: Track,
    reference_track: Track,
    warp_map: WarpMap,
    output_path: Path,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[Path, int] | None:
    """Apply the warp map to the audio track.

    Returns:
        (Path to corrected audio, delay_ms) — where delay_ms is how many
        milliseconds the output file's PTS=0 must be shifted to align with
        the reference/video timeline. Or None if no ffmpeg re-encode is
        needed (muxer handles the lossless --sync case directly).
    """
    if warp_map.is_linear_drift and _can_use_lossless_sync(warp_map):
        # Lossless path — handled by mkvmerge --sync in muxer.py.
        if progress_cb:
            if warp_map.segments:
                seg = warp_map.segments[0]
                ref_dur = seg.t_ref_end - seg.t_ref_start
                sel_dur = seg.t_sel_end - seg.t_sel_start
                factor = (sel_dur / ref_dur) if ref_dur > 0 else 1.0
                progress_cb(
                    f"Linear drift (speed factor {factor:.6f}) — "
                    f"will use lossless mkvmerge --sync"
                )
            else:
                progress_cb("No audio correction needed (perfect sync)")
        return None

    if progress_cb:
        if warp_map.is_linear_drift:
            progress_cb(
                "Linear drift with unmatched edges — rendering trimmed audio with ffmpeg"
            )
        else:
            progress_cb(
                f"Cuts detected ({len(warp_map.segments)} segments) — re-encoding audio"
            )

    codec = audio_track.codec.lower()
    encoder = _ENCODABLE_CODECS.get(codec)
    warned_fallback = False
    if encoder is None:
        encoder = _FALLBACK_CODEC
        warned_fallback = True

    output_audio = output_path / f"audio_warped.{_encoder_ext(encoder)}"
    valid_segments = _validate_segments(warp_map.segments, progress_cb)
    if not valid_segments:
        raise RuntimeError(
            "No valid warp segments survived validation — "
            "cannot apply audio correction (likely noisy sync anchors)."
        )

    audio_pieces = _build_audio_pieces(valid_segments, warp_map.ref_duration)
    gap_fill_count = sum(1 for piece in audio_pieces if piece.source == "reference")
    if gap_fill_count and progress_cb:
        progress_cb(f"Filling {gap_fill_count} missing section(s) with reference audio")
    _apply_warp_ffmpeg(
        selected_track=audio_track,
        reference_track=reference_track,
        audio_pieces=audio_pieces,
        output_path=output_audio,
        encoder=encoder,
        progress_cb=progress_cb,
    )

    # Piece construction is done in reference-video time, so the rendered file
    # starts at t_ref=0 and usually needs no mux-time shift.
    delay_ms = 0

    if warned_fallback and progress_cb:
        progress_cb(
            f"Warning: codec '{codec}' not directly encodable — output audio is AAC 320k"
        )

    return output_audio, delay_ms


def _apply_warp_ffmpeg(
    selected_track: Track,
    reference_track: Track,
    audio_pieces: list[AudioPiece],
    output_path: Path,
    encoder: str,
    progress_cb: Callable[[str], None] | None,
) -> None:
    """Build an ffmpeg command that remaps selected audio and fills holes from reference audio."""
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    normalize_filter = _build_normalize_filter(selected_track, reference_track)

    selected_input = f"0:{selected_track.stream_index}"
    reference_input = f"1:{reference_track.stream_index}"

    for i, piece in enumerate(audio_pieces):
        input_label = selected_input if piece.source == "selected" else reference_input
        trim = (
            f"[{input_label}]atrim=start={piece.src_start:.6f}:end={piece.src_end:.6f},"
            f"asetpts=PTS-STARTPTS"
        )

        filters = [trim]
        if piece.source == "selected":
            filters.append(_build_atempo_chain(piece.speed_factor))
        filters.append(normalize_filter)

        label = f"seg{i}"
        filter_parts.append(",".join(filters) + f"[{label}]")
        concat_inputs.append(f"[{label}]")

    concat_filter = "".join(f"{inp}" for inp in concat_inputs)
    concat_filter += f"concat=n={len(concat_inputs)}:v=0:a=1[aout]"
    filter_parts.append(concat_filter)

    filter_complex = ";".join(filter_parts)

    bitrate_args: list[str] = []
    if encoder in (_FALLBACK_CODEC, "aac"):
        bitrate_args = ["-b:a", _FALLBACK_BITRATE]
    elif selected_track.bit_rate:
        bitrate_args = ["-b:a", str(selected_track.bit_rate)]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(selected_track.file_path),
        "-i", str(reference_track.file_path),
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", encoder,
        *bitrate_args,
        str(output_path),
    ]

    if progress_cb:
        progress_cb(
            f"ffmpeg re-encode: {len(audio_pieces)} piece(s) → {output_path.name}"
        )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio warp failed:\n{result.stderr[-2000:]}")


def _validate_segments(
    segments: list[WarpSegment],
    progress_cb: Callable[[str], None] | None,
) -> list[WarpSegment]:
    """Drop degenerate or absurdly-scaled segments.

    A common failure mode on dubbed audio is an anchor-derived segment with
    a tiny ref_dur but a huge sel_dur (or vice versa). Feeding such a segment
    to atempo produces a chain that compresses/expands by orders of magnitude
    — e.g. a speed factor of 120 compresses the whole 2h audio into 60s of
    output. Skip these so at worst the final mux has a missing piece rather
    than completely broken audio.
    """
    valid: list[WarpSegment] = []
    skipped_reasons: list[str] = []

    for seg in segments:
        ref_dur = seg.t_ref_end - seg.t_ref_start
        sel_dur = seg.t_sel_end - seg.t_sel_start

        if ref_dur <= 0 or sel_dur <= 0:
            continue  # zero-duration pieces are expected at cut boundaries
        if ref_dur < _MIN_SEGMENT_SEC:
            skipped_reasons.append(
                f"tiny ref_dur={ref_dur:.3f}s at t_ref={seg.t_ref_start:.2f}s"
            )
            continue
        speed = sel_dur / ref_dur
        if not (_MIN_SEGMENT_SPEED <= speed <= _MAX_SEGMENT_SPEED):
            skipped_reasons.append(
                f"speed={speed:.3f} out of range at t_ref={seg.t_ref_start:.2f}s "
                f"(ref_dur={ref_dur:.2f}s, sel_dur={sel_dur:.2f}s)"
            )
            continue
        valid.append(seg)

    if skipped_reasons and progress_cb:
        progress_cb(
            f"Skipped {len(skipped_reasons)} invalid segment(s): "
            + "; ".join(skipped_reasons[:3])
            + ("…" if len(skipped_reasons) > 3 else "")
        )

    return valid


def _build_audio_pieces(
    segments: list[WarpSegment],
    ref_duration: float,
) -> list[AudioPiece]:
    """Build concat pieces in reference timeline order.

    Selected-audio segments are stretched/compressed to their target reference
    span. Any gap in the reference timeline is filled from the reference audio.
    Gaps in the selected timeline are simply skipped, as that content does not
    exist in the output video timeline.
    """
    pieces: list[AudioPiece] = []
    cursor = 0.0

    for seg in segments:
        if seg.t_ref_start > cursor:
            pieces.append(
                AudioPiece(
                    source="reference",
                    src_start=cursor,
                    src_end=seg.t_ref_start,
                    ref_start=cursor,
                    ref_end=seg.t_ref_start,
                )
            )

        pieces.append(
            AudioPiece(
                source="selected",
                src_start=seg.t_sel_start,
                src_end=seg.t_sel_end,
                ref_start=seg.t_ref_start,
                ref_end=seg.t_ref_end,
                speed_factor=seg.speed_factor,
            )
        )
        cursor = seg.t_ref_end

    if ref_duration > cursor:
        pieces.append(
            AudioPiece(
                source="reference",
                src_start=cursor,
                src_end=ref_duration,
                ref_start=cursor,
                ref_end=ref_duration,
            )
        )

    return [piece for piece in pieces if piece.src_end > piece.src_start]


def _can_use_lossless_sync(warp_map: WarpMap) -> bool:
    if not warp_map.is_linear_drift:
        return False
    if not warp_map.segments:
        return True

    seg = warp_map.segments[0]
    return (
        abs(seg.t_ref_start) <= _EDGE_TOLERANCE_SEC
        and abs(seg.t_sel_start) <= _EDGE_TOLERANCE_SEC
        and abs(seg.t_ref_end - warp_map.ref_duration) <= _EDGE_TOLERANCE_SEC
        and abs(seg.t_sel_end - warp_map.sel_duration) <= _EDGE_TOLERANCE_SEC
    )


def _build_atempo_chain(speed: float) -> str:
    """Build a chain of atempo filters to achieve the target speed ratio.

    atempo is bounded to [0.5, 2.0], so values outside require chaining.
    """
    filters: list[str] = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return ",".join(filters)


def _build_normalize_filter(selected_track: Track, reference_track: Track) -> str:
    sample_rate = selected_track.sample_rate or reference_track.sample_rate or 48000
    channel_layout = _pick_concat_channel_layout(selected_track, reference_track)
    if channel_layout is not None:
        return (
            "aformat="
            f"sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts={channel_layout}"
        )
    return f"aformat=sample_fmts=fltp:sample_rates={sample_rate}"


def _pick_concat_channel_layout(
    selected_track: Track,
    reference_track: Track,
) -> str | None:
    selected_layout = _normalize_channel_layout(selected_track.channel_layout)
    reference_layout = _normalize_channel_layout(reference_track.channel_layout)

    if selected_layout and selected_layout == reference_layout:
        return selected_layout

    selected_channels = selected_track.channels
    reference_channels = reference_track.channels

    if selected_channels and reference_channels:
        common_channels = min(selected_channels, reference_channels)
        return _channel_layout_from_count(common_channels)

    return selected_layout or reference_layout


def _normalize_channel_layout(layout: str | None) -> str | None:
    if not layout:
        return None
    return layout.lower().replace("(side)", "").strip()


def _channel_layout_from_count(channels: int) -> str | None:
    return {
        1: "mono",
        2: "stereo",
        6: "5.1",
        8: "7.1",
    }.get(channels)


def _encoder_ext(encoder: str) -> str:
    return {
        "aac": "aac",
        "libmp3lame": "mp3",
        "libopus": "opus",
        "libvorbis": "ogg",
        "flac": "flac",
        "ac3": "ac3",
        "eac3": "eac3",
        "pcm_s16le": "wav",
        "pcm_s24le": "wav",
    }.get(encoder, "mka")
