from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Track:
    file_path: Path
    stream_index: int
    track_type: str  # "video" | "audio" | "subtitle"
    codec: str
    language: str | None = None
    title: str | None = None

    # Video-specific
    width: int | None = None
    height: int | None = None
    fps: str | None = None

    # Audio-specific
    channels: int | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    channel_layout: str | None = None  # e.g. "stereo", "5.1", "7.1"
    codec_profile: str | None = None   # e.g. "DTS-MA", "LC" (AAC), "Atmos"

    # Subtitle-specific
    sub_format: str | None = None  # "srt" | "ass" | "pgs" | "vobsub"

    # Disposition flags (all track types, most useful for subtitles)
    forced: bool = False
    default_track: bool = False
    hearing_impaired: bool = False  # SDH

    @property
    def audio_codec_display(self) -> str:
        """Human-readable codec name, incorporating profile where useful."""
        codec = self.codec.lower()
        profile = (self.codec_profile or "").upper()
        mapping = {
            "ac3":    "Dolby Digital",
            "eac3":   "Dolby Digital+",
            "truehd": "TrueHD Atmos" if "ATMOS" in profile else "TrueHD",
            "dts":    profile if profile in ("DTS-MA", "DTS-HRA", "DTS:X") else "DTS",
            "aac":    "AAC",
            "mp3":    "MP3",
            "flac":   "FLAC",
            "opus":   "Opus",
            "vorbis": "Vorbis",
            "pcm_s16le": "PCM 16bit",
            "pcm_s24le": "PCM 24bit",
        }
        return mapping.get(codec, self.codec.upper())

    @property
    def audio_layout_display(self) -> str:
        """Human-readable channel layout (e.g. '5.1', 'stereo', '7.1')."""
        if self.channel_layout:
            # Normalize common ffprobe strings
            cl = self.channel_layout.lower().replace("(side)", "").strip()
            return {
                "mono": "Mono",
                "stereo": "Stereo",
                "2.1": "2.1",
                "3.0": "3.0",
                "3.0(back)": "3.0",
                "4.0": "4.0",
                "quad": "4.0",
                "5.0": "5.0",
                "5.1": "5.1",
                "6.0": "6.0",
                "6.1": "6.1",
                "7.0": "7.0",
                "7.1": "7.1",
                "octagonal": "7.1",
            }.get(cl, cl)
        # Fallback from channel count
        return {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(
            self.channels or 0, f"{self.channels}ch" if self.channels else ""
        )

    @property
    def audio_bitrate_display(self) -> str:
        if not self.bit_rate:
            return ""
        kbps = self.bit_rate // 1000
        return f"{kbps} kbps"

    @property
    def sub_format_display(self) -> str:
        """Human-readable subtitle format name."""
        return {
            "srt":    "SRT",
            "ass":    "ASS",
            "pgs":    "PGS",
            "vobsub": "VOBSUB",
        }.get(self.sub_format or "", (self.sub_format or self.codec).upper())

    @property
    def sub_badges(self) -> list[str]:
        """Ordered list of disposition badge strings for display."""
        badges: list[str] = []
        if self.forced:
            badges.append("Forced")
        if self.default_track:
            badges.append("Default")
        if self.hearing_impaired:
            badges.append("SDH")
        if self.is_bitmap_subtitle:
            badges.append("Bitmap")
        return badges

    @property
    def display_label(self) -> str:
        parts: list[str] = [f"#{self.stream_index}", self.codec.upper()]
        if self.track_type == "video" and self.width and self.height:
            parts.append(f"{self.width}x{self.height}")
            if self.fps:
                parts.append(f"{self.fps}fps")
        elif self.track_type == "audio":
            if self.channels:
                parts.append(f"{self.channels}ch")
            if self.sample_rate:
                parts.append(f"{self.sample_rate // 1000}kHz")
        if self.language:
            parts.append(f"[{self.language}]")
        if self.title:
            parts.append(f'"{self.title}"')
        return "  ".join(parts)

    @property
    def is_bitmap_subtitle(self) -> bool:
        return self.sub_format in ("pgs", "vobsub", "dvdsub", "hdmv_pgs_subtitle")


@dataclass
class WarpSegment:
    """A segment of the warp map: audio_sel time [t_sel_start, t_sel_end] maps to ref time [t_ref_start, t_ref_end]."""
    t_ref_start: float
    t_ref_end: float
    t_sel_start: float
    t_sel_end: float

    @property
    def speed_factor(self) -> float:
        ref_dur = self.t_ref_end - self.t_ref_start
        sel_dur = self.t_sel_end - self.t_sel_start
        if sel_dur == 0:
            return 1.0
        return sel_dur / ref_dur


@dataclass
class WarpMap:
    """Time warp map from selected audio timeline to reference audio timeline."""
    segments: list[WarpSegment] = field(default_factory=list)
    ref_duration: float = 0.0
    sel_duration: float = 0.0

    @property
    def is_linear_drift(self) -> bool:
        """True if the warp is a simple linear speed factor (no discrete cuts)."""
        if len(self.segments) <= 1:
            return True
        # Check for gaps in the reference timeline (= cuts in reference)
        # or gaps in selected timeline (= cuts in selected audio)
        for i in range(1, len(self.segments)):
            prev = self.segments[i - 1]
            curr = self.segments[i]
            ref_gap = curr.t_ref_start - prev.t_ref_end
            sel_gap = curr.t_sel_start - prev.t_sel_end
            if abs(ref_gap) > 0.5 or abs(sel_gap) > 0.5:
                return False
        return True

    @property
    def global_speed_factor(self) -> float:
        if self.sel_duration == 0:
            return 1.0
        return self.sel_duration / self.ref_duration


@dataclass
class TrackSelection:
    video: Track
    audio: Track
    subtitle: Track | None
    reference_audio: Track  # first audio track from the video source file
    output_path: Path = Path("output.mkv")
