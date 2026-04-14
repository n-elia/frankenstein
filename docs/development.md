# Development

## Setup

```bash
git clone https://github.com/n-elia/frankenstein.git
cd frankenstein
uv sync
```

## Running tests

```bash
uv run --group dev pytest tests -v
```

Some tests generate a synthetic MKV fixture on first run (requires `ffmpeg` with `libx264`). The fixture is cached under `tests/fixtures/movies/` and excluded from version control. See [test-strategy.md](test-strategy.md) for the full test architecture and file inventory.

## Project structure

```
src/frankenstein/
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

## How audio synchronisation works

See [architecture.md](architecture.md) for the full design document. The short version:

1. Both audio tracks are extracted as mono 16 kHz WAVs
2. Chroma CENS features are computed with [librosa](https://librosa.org/)
3. A coarse global offset is found via multi-band cross-correlation ([scipy](https://scipy.org/))
4. Chunked local alignment refines the offset into anchor points
5. Anchors are smoothed and classified:
   - **Linear drift** — single segment, corrected losslessly with `mkvmerge --sync` when possible
   - **Cuts detected** — multiple segments, rendered via ffmpeg with gap fill from the reference audio
6. Subtitles are remapped through the same warp map

### Confidence score

A confidence value (0--1) is reported after alignment. It measures feature-match strength, not a calibrated probability of correctness. Weak scores are a cue to spot-check the result.

### Subtitle support

| Format | Realignment | Notes |
|---|---|---|
| SRT | Full (per-cue timestamp remapping) | |
| ASS / SSA | Full (per-event timestamp remapping) | |
| PGS / VOBSUB | Constant offset only | Bitmap format — per-frame manipulation not supported |
