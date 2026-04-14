from __future__ import annotations

import json
import subprocess
from pathlib import Path

from frankenstein.models import Track

_SUBTITLE_FORMAT_MAP = {
    "subrip": "srt",
    "ass": "ass",
    "ssa": "ass",
    "hdmv_pgs_subtitle": "pgs",
    "dvd_subtitle": "vobsub",
    "dvdsub": "vobsub",
    "mov_text": "srt",
    "webvtt": "srt",
}


def inspect_file(file_path: Path) -> list[Track]:
    """Return a list of Track objects for all streams in a MKV file."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    tracks: list[Track] = []
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type not in ("video", "audio", "subtitle"):
            continue

        tags = stream.get("tags", {})
        language = tags.get("language") or tags.get("LANGUAGE")
        title = tags.get("title") or tags.get("TITLE")
        codec_name = stream.get("codec_name", "unknown")
        index = stream.get("index", 0)

        disposition = stream.get("disposition", {})

        track = Track(
            file_path=file_path,
            stream_index=index,
            track_type=codec_type,
            codec=codec_name,
            language=language if language and language != "und" else None,
            title=title,
            forced=bool(disposition.get("forced", 0)),
            default_track=bool(disposition.get("default", 0)),
            hearing_impaired=bool(disposition.get("hearing_impaired", 0)),
        )

        if codec_type == "video":
            track.width = stream.get("width")
            track.height = stream.get("height")
            r_frame_rate = stream.get("r_frame_rate", "")
            if r_frame_rate and "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                if int(den) > 0:
                    fps_val = int(num) / int(den)
                    track.fps = f"{fps_val:.3f}".rstrip("0").rstrip(".")

        elif codec_type == "audio":
            track.channels = stream.get("channels")
            track.sample_rate = int(stream["sample_rate"]) if "sample_rate" in stream else None
            br = stream.get("bit_rate")
            track.bit_rate = int(br) if br else None
            track.channel_layout = stream.get("channel_layout")
            track.codec_profile = stream.get("profile")

        elif codec_type == "subtitle":
            track.sub_format = _SUBTITLE_FORMAT_MAP.get(codec_name, codec_name)

        tracks.append(track)

    return tracks


def get_tracks_by_type(tracks: list[Track], track_type: str) -> list[Track]:
    return [t for t in tracks if t.track_type == track_type]


def get_reference_audio(file_path: Path, all_tracks: list[Track]) -> Track | None:
    """Return the first audio track from the given file."""
    for t in all_tracks:
        if t.file_path == file_path and t.track_type == "audio":
            return t
    return None
