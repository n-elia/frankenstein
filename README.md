<p align="center">
  <img src="assets/logo.png" alt="Frankenstein" width="200"/>
</p>


<p align="center">
  <em>Frankenstein - Stitch the best video and audio from different MKV files into one perfectly synced copy.</em>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"/></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/requires-ffmpeg%20%7C%20mkvtoolnix-orange.svg" alt="ffmpeg | mkvtoolnix"/>
</p>

---

## The problem

You have two versions of the same film:

- **`movie_hq.mkv`** — **high-quality video** (H.265 1080p) but only the original language audio
- **`movie_lang.mkv`** — **your preferred language dub**, but low-quality video

Naively muxing them together leaves the audio out of sync: the two versions may differ due to **gradual drift** (e.g. 29.97 vs 30 fps sources) or **discrete cuts** (censored/missing scenes in one version).

`frankensync` detects and corrects both automatically, then produces a new MKV with the tracks you chose.

## Quick start

```bash
# 1. Install system tools
sudo apt install ffmpeg mkvtoolnix   # or: brew install ffmpeg mkvtoolnix

# 2. Install frankensync
uv tool install git+https://github.com/n-elia/frankenstein

# 3. Run it
frankensync movie_hq.mkv movie_lang.mkv -o movie_final.mkv
```

## How it works (in brief)

The **video track is the master timeline**. The tool extracts the first audio track from the video source file (the "reference audio"), compares it against the audio you picked via chroma-based cross-correlation, and warps the selected audio -- and any subtitle -- to match the video's timeline exactly.

```
Reference audio  ──────────────[A]────────────[B]─────────────────
(from the video source — the master timeline)

                             ↕ warp map ↕

Selected audio   ──────[A']─────────────[B']────────[cut]──────────
(from the other file — warped to match the master)
```

The alignment builds a compact list of linear segments; large jumps in either timeline indicate a discrete cut.

### Why not just `mkvmerge`?

Plain muxing stream-copies every track as-is. If the two files were mastered from different sources (different frame rates, different edits, broadcast censoring), the audio will drift or jump out of sync. `frankensync` detects these differences acoustically and corrects them before muxing.

## Features

- **Interactive TUI** (powered by [Textual](https://textual.textualize.io/)) to pick video, audio, and subtitle tracks across both source files
- **Chroma cross-correlation** to compute the exact time mapping between the two audio versions ([librosa](https://librosa.org/) features, [scipy](https://scipy.org/) correlation)
- **No video re-encoding** — the video track is always stream-copied
- **Lossless audio correction** for pure drift (`mkvmerge --sync` adjusts PTS timestamps only)
- **Re-encoding only when cuts require it** — uses the source codec where possible, falls back to AAC, and fills censored gaps with the reference audio
- **Subtitle realignment** — SRT and ASS/SSA timestamps are remapped using the same warp map as the audio

## Usage

```bash
frankensync <file1.mkv> <file2.mkv> [--output PATH]
```

From a local checkout, prefix with `uv run`:

```bash
uv run frankensync <file1.mkv> <file2.mkv> [--output PATH]
```

Running **with no arguments** inside a directory of MKVs automatically discovers all files and defaults the output into the same directory:

```bash
cd ~/Movies/some-title/
frankensync
```

| Argument | Description |
|---|---|
| `file1` | First MKV (optional in directory mode) |
| `file2` | Second MKV (optional in directory mode) |
| `--output`, `-o` | Output MKV path. Defaults to `<stem>_merged.mkv`. |

### The TUI

The tool opens an interactive track selector:

```
┌─────────────────────────────────────────────────────┐
│  Frankenstein             Step 1/3 — Video Track    │
├─────────────────────────────────────────────────────┤
│  movie_hq.mkv                                       │
│  > [●] #0  HEVC  1920x1080  23.976fps  [eng]        │
│    [ ] #1  H264  1280x720   23.976fps               │
│                                                     │
│  movie_lang.mkv                                     │
│    [ ] #0  H264  1280x720   23.976fps               │
├─────────────────────────────────────────────────────┤
│  ↑↓ navigate   SPACE select   ENTER confirm         │
└─────────────────────────────────────────────────────┘
```

After confirming all three selections (video, audio, subtitle), the pipeline:

1. Extracts the **reference audio** (from the video source) and the **selected audio** as mono 16 kHz WAVs
2. Computes chroma features with [librosa](https://librosa.org/)
3. Aligns them via multi-band cross-correlation ([scipy](https://scipy.org/))
4. Classifies the divergence:
   - **Linear drift** → lossless `mkvmerge --sync` (no re-encode)
   - **Cuts detected** → ffmpeg re-encode with gap fill from reference audio
5. Applies the same warp map to subtitle timestamps
6. Muxes everything with `mkvmerge`

### Confidence score

After alignment a confidence score (0--1) is reported. It measures how strongly the chroma features matched. Weak scores are a cue to spot-check the output, especially when cut detection was involved.

### Subtitle support

| Format | Realignment | Notes |
|---|---|---|
| SRT | Full (per-cue timestamp remapping) | |
| ASS / SSA | Full (per-event timestamp remapping) | |
| PGS / VOBSUB | Constant offset only | Bitmap format -- per-frame manipulation not supported |

## Requirements

| Dependency | Install |
|---|---|
| **ffmpeg** | `apt install ffmpeg` / `brew install ffmpeg` |
| **mkvtoolnix** | `apt install mkvtoolnix` / `brew install mkvtoolnix` |
| **Python >= 3.11** | via [uv](https://github.com/astral-sh/uv), pyenv, or system package manager |

## Installation

```bash
# As a uv tool (recommended)
uv tool install git+https://github.com/n-elia/frankenstein

# Editable install for development
git clone https://github.com/n-elia/frankenstein.git
cd frankenstein
uv tool install --editable .
```

## Development

```bash
git clone https://github.com/n-elia/frankenstein.git
cd frankenstein
uv sync
uv run python -m unittest discover tests -v
```

## Project structure

```
src/film_tracks_aligner/
├── cli.py              # Typer entry point & pipeline orchestration
├── models.py           # Track, TrackSelection, WarpMap dataclasses
├── mkv/
│   ├── inspector.py    # ffprobe wrapper — reads track metadata
│   ├── extractor.py    # ffmpeg/mkvextract track extraction
│   └── muxer.py        # mkvmerge output assembly
├── sync/
│   ├── analyzer.py     # librosa feature extraction (chroma, onsets)
│   ├── aligner.py      # Cross-correlation alignment & warp map
│   └── applier.py      # Audio warp application (lossless or re-encode)
├── subtitle/
│   └── adjuster.py     # SRT / ASS timestamp remapping
└── tui/
    ├── app.py           # Textual App — 3-step selection flow
    └── screens/
        ├── track_select.py  # Interactive track list screen
        ├── theme_select.py  # Theme picker
        └── processing.py    # Progress & log screen
```

## License

[MIT](LICENSE)
