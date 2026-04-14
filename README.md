# Frankenstein

A CLI tool to merge video and audio tracks from two separate MKV files, with automatic audio synchronization.

## The problem it solves

You have two versions of the same film:
- `movie_hq.mkv` — high-quality video (H.265 1080p) but only the original language audio
- `movie_lang.mkv` — your preferred language audio, but low-quality video

Naively muxing them together leaves the audio out of sync: the two versions may differ due to **gradual drift** (e.g. 29.97 vs 30 fps sources) or **discrete cuts** (censored/missing scenes in one version).

`frankensync` detects and corrects both automatically, then produces a new MKV with the tracks you chose.

## Features

- Interactive TUI (powered by [Textual](https://textual.textualize.io/)) to select video, audio, and subtitle tracks across both source files
- Acoustic fingerprinting + Dynamic Time Warping (DTW) to compute the exact time mapping between the two audio versions
- **No video re-encoding** — the video track is always stream-copied
- **Lossless audio correction** for pure drift (uses `mkvmerge --sync` to adjust PTS timestamps only)
- Re-encoding only when discrete cuts require it — uses the source codec where possible, falls back to AAC, and fills missing censored sections with the original/reference audio
- Subtitle rialignment: SRT and ASS/SSA timestamps are remapped using the same warp map as the audio

## Requirements

### System tools

```bash
# Debian/Ubuntu
sudo apt install ffmpeg mkvtoolnix
```

```bash
# macOS
brew install ffmpeg mkvtoolnix
```

### Python

Python 3.14+ and [uv](https://github.com/astral-sh/uv) (or pip).

## Installation

### As a uv tool

```bash
uv tool install .
```

For editable installs while developing:

```bash
uv tool install --editable .
```

### From source

```bash
git clone <repository-url>
cd <repository-directory>
uv sync
```

Or install directly:

```bash
uv pip install .
```

## Usage

If installed as a uv tool:

```bash
frankensync <file1.mkv> <file2.mkv> [--output PATH]
```

Or, from a local checkout, run the same command with `uv run`:

```bash
uv run frankensync <file1.mkv> <file2.mkv> [--output PATH]
```

If you run the command **with no positional arguments** inside a directory containing `.mkv` files, the tool automatically loads **all MKVs in the current directory** into the TUI and defaults the output file into that same directory.

```bash
frankensync
```

| Argument | Description |
|---|---|
| `file1` | First MKV (optional when using directory mode) |
| `file2` | Second MKV (optional when using directory mode) |
| `--output`, `-o` | Output MKV path. Without `--output`, explicit two-file mode defaults to `<file1_stem>_merged.mkv`; directory mode defaults to `<selected_video_stem>_merged.mkv` in the current directory. |

### Example

```bash
frankensync movie_hq.mkv movie_lang.mkv --output ~/Videos/movie_final.mkv
```

The tool opens an interactive TUI:

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

After confirming all three selections (video → audio → subtitle), the tool:

1. Extracts both audio tracks as mono 16kHz WAV for analysis
2. Computes chroma + onset features with [librosa](https://librosa.org/)
3. Aligns them with windowed DTW ([dtaidistance](https://dtaidistance.readthedocs.io/))
4. Classifies the divergence:
   - **Linear drift only** → lossless `mkvmerge --sync` (no re-encode)
   - **Cuts detected** → ffmpeg `concat` + `atempo` filter chain (re-encode, same codec)
5. Applies the same time warp to subtitle timestamps
6. Muxes everything with `mkvmerge`

## How audio synchronization works

```
Reference audio  ──────────────[A]────────────[B]─────────────────
                                ↕ DTW warp map ↕
Selected audio   ──────[A']─────────────[B']────────[cut]──────────
```

The DTW warp path maps every frame of the selected audio to its corresponding position in the reference audio. From this path the tool builds a compact list of linear segments; large jumps in either timeline indicate a discrete cut.

### Confidence score

After alignment a confidence score (0-1) is reported. It measures how strongly the analysis features matched, not whether the final mux is definitely wrong. Weak scores are most useful as a cue to spot-check the result, especially when cut detection or larger timing adjustments were required.

### Subtitle support

| Format | Rialignment | Notes |
|---|---|---|
| SRT | Full (per-cue timestamp remapping) | |
| ASS / SSA | Full (per-event timestamp remapping) | |
| PGS / VOBSUB | Constant offset only | Bitmap format — per-frame manipulation not supported |

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
│   ├── analyzer.py     # librosa feature extraction
│   ├── aligner.py      # DTW alignment & warp map computation
│   └── applier.py      # audio warp application (lossless or re-encode)
├── subtitle/
│   └── adjuster.py     # SRT / ASS timestamp remapping
└── tui/
    ├── app.py           # Textual App, 3-step selection flow
    └── screens/
        ├── track_select.py  # Interactive track list screen
        └── processing.py    # Progress & log screen
```

## License

MIT
