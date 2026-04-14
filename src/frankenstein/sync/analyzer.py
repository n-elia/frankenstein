from __future__ import annotations

from pathlib import Path
from typing import Callable

import librosa
import numpy as np


# Analysis sample rate.
# 16 kHz keeps Nyquist (8 kHz) well above the max CQT frequency (~4 kHz)
# used by chroma_cens.
ANALYSIS_SR = 16000

# Hop length controls time resolution per frame:
#   frame_duration = HOP_LENGTH / ANALYSIS_SR = 2048 / 16000 = 128 ms
# A 44-minute episode → ~20 500 frames (vs ~82 000 at hop=512).
# A 2-hour movie     → ~56 000 frames.
HOP_LENGTH = 2048

N_CHROMA = 12

# Derived constant used by aligner
SEC_PER_FRAME = HOP_LENGTH / ANALYSIS_SR  # 0.128 s


def extract_features(
    wav_path: Path,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Load a WAV file and extract chroma_cens and onset_strength features.

    Returns:
        chroma:    (N_CHROMA, T) array
        onset_env: (T,) onset strength envelope
        duration:  total audio duration in seconds
    """
    if progress_cb:
        progress_cb(f"Loading audio: {wav_path.name}")

    y, sr = librosa.load(str(wav_path), sr=ANALYSIS_SR, mono=True)
    duration = len(y) / sr

    if progress_cb:
        progress_cb("Extracting chroma features")

    chroma = librosa.feature.chroma_cens(
        y=y,
        sr=sr,
        hop_length=HOP_LENGTH,
        n_chroma=N_CHROMA,
    )

    if progress_cb:
        progress_cb("Extracting onset strength")

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)

    return chroma, onset_env, duration


def frames_to_time(n_frames: int, sr: int = ANALYSIS_SR, hop: int = HOP_LENGTH) -> np.ndarray:
    """Convert frame indices to time in seconds."""
    return librosa.frames_to_time(np.arange(n_frames), sr=sr, hop_length=hop)
