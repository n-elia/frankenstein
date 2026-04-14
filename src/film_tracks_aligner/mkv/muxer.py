from __future__ import annotations

import subprocess
from pathlib import Path

from film_tracks_aligner.models import TrackSelection, WarpMap


def mux_output(
    selection: TrackSelection,
    warped_audio: tuple[Path, int] | None,
    adjusted_subtitle_path: Path | None,
    subtitle_delay_ms: int,
    warp_map: WarpMap,
    output_path: Path,
) -> Path:
    """Build the final MKV using mkvmerge.

    Strategy:
    - Video: always stream-copied from the source file
    - Audio:
        - If warped_audio is provided (ffmpeg re-encoded): use it, with a
          --sync delay so that its PTS=0 lines up with the video timeline
        - If only linear drift: use mkvmerge --sync for lossless timestamp
          adjustment (delay + speed multiplier derived from the warp segment)
        - If no correction needed: stream-copy the selected audio
    - Subtitle: use adjusted_subtitle_path if provided, else stream-copy
    """
    cmd = ["mkvmerge", "-o", str(output_path)]

    # --- Video track ---
    video = selection.video
    cmd += [
        "--video-tracks", str(video.stream_index),
        "--no-audio", "--no-subtitles",
        str(video.file_path),
    ]

    # --- Audio track ---
    audio = selection.audio
    if warped_audio is not None:
        # Re-encoded audio file. The first sample of the file corresponds to
        # ref time = delay_ms/1000; apply a delay so it aligns with video.
        warped_path, delay_ms = warped_audio
        if delay_ms:
            cmd += ["--sync", f"0:{delay_ms}"]
        cmd += [str(warped_path)]
    else:
        sync_spec = _build_linear_sync(warp_map, audio_track_id=audio.stream_index)
        if sync_spec is not None:
            cmd += [
                "--audio-tracks", str(audio.stream_index),
                "--no-video", "--no-subtitles",
                "--sync", sync_spec,
                str(audio.file_path),
            ]
        else:
            # No correction needed
            cmd += [
                "--audio-tracks", str(audio.stream_index),
                "--no-video", "--no-subtitles",
                str(audio.file_path),
            ]

    # --- Subtitle track ---
    subtitle = selection.subtitle
    if subtitle is not None:
        if adjusted_subtitle_path is not None:
            if subtitle_delay_ms:
                cmd += ["--sync", f"0:{subtitle_delay_ms}"]
            cmd += [str(adjusted_subtitle_path)]
        else:
            cmd += [
                "--subtitle-tracks", str(subtitle.stream_index),
                "--no-video", "--no-audio",
                str(subtitle.file_path),
            ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 1):  # mkvmerge exits 1 for warnings
        raise RuntimeError(
            f"mkvmerge failed (exit {result.returncode}):\n{result.stderr}"
        )
    return output_path


def _build_linear_sync(warp_map: WarpMap, audio_track_id: int) -> str | None:
    """Derive an mkvmerge ``--sync TID:delay,p/q`` spec from a linear warp.

    Maps each original sel PTS ``t`` to a new PTS::

        new_t = t * (ref_dur / sel_dur) + (t_ref_start - t_sel_start * ref_dur / sel_dur)

    so that ``t == t_sel_start`` lands exactly at ``t_ref_start`` in the
    video timeline, and the slope within the segment matches the sel→ref
    ratio. Returns None if no correction is needed or cannot be expressed
    with a single linear transform (caller should fall back to a plain
    stream-copy or to the ffmpeg-warped path).
    """
    if not warp_map.is_linear_drift or not warp_map.segments:
        return None

    seg = warp_map.segments[0]
    ref_dur = seg.t_ref_end - seg.t_ref_start
    sel_dur = seg.t_sel_end - seg.t_sel_start
    if ref_dur <= 0 or sel_dur <= 0:
        return None

    pts_multiplier = ref_dur / sel_dur
    delay_sec = seg.t_ref_start - seg.t_sel_start * pts_multiplier
    delay_ms = int(round(delay_sec * 1000))

    if abs(pts_multiplier - 1.0) < 1e-6 and delay_ms == 0:
        return None  # no-op

    p, q = _float_to_rational(pts_multiplier, max_denominator=100000)
    return f"{audio_track_id}:{delay_ms},{p}/{q}"


def _float_to_rational(value: float, max_denominator: int = 100000) -> tuple[int, int]:
    """Convert a float to an integer ratio p/q using the Stern-Brocot tree / Farey sequence approach."""
    from fractions import Fraction
    frac = Fraction(value).limit_denominator(max_denominator)
    return frac.numerator, frac.denominator
