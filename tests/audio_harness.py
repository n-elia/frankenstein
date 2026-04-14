from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from frankenstein.models import Track


def write_tone_sequence(
    path: Path,
    segments: list[tuple[float, float]],
    sample_rate: int = 16000,
) -> None:
    """Write a deterministic mono tone sequence for synthetic sync scenarios."""
    parts: list[np.ndarray] = []
    for frequency, duration in segments:
        t = np.linspace(0.0, duration, int(sample_rate * duration), endpoint=False)
        parts.append(0.25 * np.sin(2 * np.pi * frequency * t))
    sf.write(path, np.concatenate(parts), sample_rate)


def dominant_frequency(samples: np.ndarray, sample_rate: int) -> float:
    window = np.hanning(len(samples))
    spectrum = np.fft.rfft(samples * window)
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)
    return float(freqs[int(np.argmax(np.abs(spectrum)))])


def make_pcm_track(path: Path, sample_rate: int = 16000) -> Track:
    return Track(
        file_path=path,
        stream_index=0,
        track_type="audio",
        codec="pcm_s16le",
        channels=1,
        sample_rate=sample_rate,
        channel_layout="mono",
    )


MOVIE_FIXTURE = Path("tests/fixtures/movies/test_movie_en.mkv")

# 90 seconds of pitched tones, one note per 5-second segment.  Frequencies
# are arranged to maximise chromatic distance between consecutive segments
# so that every 15-second chroma window (3 segments) has a unique tonal
# fingerprint for reliable cross-correlation alignment.
_FIXTURE_TONES: list[tuple[float, float]] = [
    (261.63, 5.0),  # C4    0-5 s
    (369.99, 5.0),  # F#4   5-10 s
    (293.66, 5.0),  # D4   10-15 s
    (415.30, 5.0),  # G#4  15-20 s
    (329.63, 5.0),  # E4   20-25 s
    (466.16, 5.0),  # A#4  25-30 s
    (392.00, 5.0),  # G4   30-35 s
    (277.18, 5.0),  # C#4  35-40 s
    (440.00, 5.0),  # A4   40-45 s
    (311.13, 5.0),  # D#4  45-50 s
    (493.88, 5.0),  # B4   50-55 s
    (349.23, 5.0),  # F4   55-60 s
    (329.63, 5.0),  # E4   60-65 s
    (261.63, 5.0),  # C4   65-70 s
    (415.30, 5.0),  # G#4  70-75 s
    (293.66, 5.0),  # D4   75-80 s
    (466.16, 5.0),  # A#4  80-85 s
    (369.99, 5.0),  # F#4  85-90 s
]


def ensure_movie_fixture() -> bool:
    """Generate the synthetic MKV test fixture if it does not exist.

    Creates a minimal MKV with a black video stream (index 0) and a mono
    PCM audio stream (index 1) containing 90 s of pitched tones.

    Returns True when the fixture is available, False otherwise.
    """
    if MOVIE_FIXTURE.exists():
        return True
    if not shutil.which("ffmpeg"):
        return False

    MOVIE_FIXTURE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="frankensync-fixture-") as tmpdir:
            wav_path = Path(tmpdir) / "audio.wav"
            write_tone_sequence(wav_path, _FIXTURE_TONES)

            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi",
                    "-i", "color=c=black:s=16x16:r=1:d=90",
                    "-i", str(wav_path),
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "pcm_s16le",
                    "-shortest",
                    str(MOVIE_FIXTURE),
                ],
                check=True,
                capture_output=True,
            )
    except (subprocess.CalledProcessError, OSError):
        return False

    return MOVIE_FIXTURE.exists()
