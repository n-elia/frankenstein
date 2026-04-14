from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from frankenstein.models import Track


def extract_audio_as_wav(track: Track, output_path: Path, sample_rate: int = 16000) -> Path:
    """Extract an audio track from a MKV file as mono WAV at the given sample rate.

    Used for audio analysis (sync detection), NOT for the final output.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(track.file_path),
        "-map", f"0:{track.stream_index}",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "wav",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def extract_audio_native(track: Track, output_path: Path) -> Path:
    """Extract an audio track from a MKV file preserving the original codec (for final output)."""
    ext = _codec_to_ext(track.codec)
    out = output_path.with_suffix(ext)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(track.file_path),
        "-map", f"0:{track.stream_index}",
        "-c:a", "copy",
        str(out),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out


def extract_subtitle(track: Track, output_dir: Path) -> Path:
    """Extract a subtitle track using mkvextract (preferred) or ffmpeg."""
    ext = _subtitle_ext(track)
    output_path = output_dir / f"subtitle_{track.stream_index}{ext}"

    # Try mkvextract first (preserves exact format)
    try:
        _extract_subtitle_mkvextract(track, output_path)
        return output_path
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fallback: ffmpeg
    _extract_subtitle_ffmpeg(track, output_path)
    return output_path


def _extract_subtitle_mkvextract(track: Track, output_path: Path) -> None:
    # mkvextract uses MKV track IDs which differ from ffprobe stream indices.
    # We rely on the fact that mkvextract track IDs match ffprobe stream index for MKV containers.
    cmd = [
        "mkvextract",
        str(track.file_path),
        "tracks",
        f"{track.stream_index}:{output_path}",
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _extract_subtitle_ffmpeg(track: Track, output_path: Path) -> None:
    codec = _subtitle_ffmpeg_encoder(track)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(track.file_path),
        "-map", f"0:{track.stream_index}",
        "-c:s", codec,
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _subtitle_ext(track: Track) -> str:
    fmt = track.sub_format or ""
    return {
        "srt": ".srt",
        "ass": ".ass",
        "pgs": ".sup",
        "vobsub": ".sub",
    }.get(fmt, ".srt")


def _subtitle_ffmpeg_encoder(track: Track) -> str:
    fmt = track.sub_format or ""
    return {
        "srt": "srt",
        "ass": "ass",
        "pgs": "copy",
        "vobsub": "copy",
    }.get(fmt, "srt")


def _codec_to_ext(codec: str) -> str:
    return {
        "ac3": ".ac3",
        "eac3": ".eac3",
        "dts": ".dts",
        "aac": ".aac",
        "mp3": ".mp3",
        "opus": ".opus",
        "vorbis": ".ogg",
        "flac": ".flac",
        "truehd": ".thd",
        "pcm_s16le": ".wav",
        "pcm_s24le": ".wav",
    }.get(codec.lower(), ".mka")
