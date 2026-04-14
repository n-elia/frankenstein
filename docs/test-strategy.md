# Test strategy

## Motivation

The suite is organized around **sync scenarios**, not source files. Early regressions showed that helper-level tests can stay green while pipeline-level routing bugs slip through -- a global cross-correlation sign mismatch and a linear-path routing bug both reached manual validation despite passing unit tests. The fix was to test *behavior the user sees*, not just internal helpers.

## Test pyramid

```
helper math  →  synthetic scenario harness  →  path-selection tests  →  fixture-backed integration
```

Each layer adds confidence at a different cost:

| Layer | What it covers | Speed | Determinism |
|---|---|---|---|
| **Helper math** | Sign conventions, warp arithmetic, segment sanity | Fast | Full |
| **Synthetic harness** | Rendered output duration, trimming, gap fill | Fast | Full |
| **Path selection** | Routing between lossless sync and ffmpeg render | Fast | Full |
| **Fixture-backed integration** | Full alignment pipeline on MKV-extracted audio | Slower | Full (synthetic fixture) |

## Scenario catalog

Coverage is driven by a catalog of named sync situations (`tests/scenarios.py`). Every correction strategy the app can choose must be represented by at least one scenario with a user-visible outcome assertion (output duration, trimming boundaries, gap fill).

Current scenarios:

| Scenario | Kind | Fixture-backed |
|---|---|---|
| `linear_full_overlap` | linear | No |
| `linear_preamble_only` | linear | Yes |
| `linear_postamble_only` | linear | Yes |
| `linear_preamble_postamble` | linear | Yes |
| `middle_hole_cut_detection` | cut_detection | Yes |

`tests/test_scenario_catalog.py` is a meta-test that verifies the catalog itself covers all core sync behaviors and that cut-detection entries are flagged as requiring media fixtures.

## Synthetic audio harness

`tests/audio_harness.py` provides shared utilities for building deterministic test audio:

- **`write_tone_sequence()`** -- generates mono WAVs from a list of `(frequency, duration)` segments. Tests compose scenarios (preamble, content, postamble, gaps) from pitched tones without depending on real media.
- **`ensure_movie_fixture()`** -- auto-generates `tests/fixtures/movies/test_movie_en.mkv` the first time a fixture-backed test runs. The MKV contains a minimal black video stream and 90 seconds of pitched tones (18 five-second segments, each at a different chroma class) arranged so every 15-second window has a unique tonal fingerprint for reliable cross-correlation. Requires `ffmpeg` with `libx264`; tests skip gracefully when the fixture cannot be created.
- **`make_pcm_track()`** / **`dominant_frequency()`** -- helpers for building `Track` objects and verifying spectral content in rendered audio.

The generated fixture is cached under `tests/fixtures/movies/` and excluded from version control via `.gitignore`.

## Test file inventory

| File | Layer | What it tests |
|---|---|---|
| `test_linear_alignment.py` | Helper math | Global cross-correlation sign convention, linear fit with negative intercept, constant-delay offset arithmetic |
| `test_cut_detection.py` | Helper math + fixture integration | Anchor-to-warp-map conversion, edge-offset recovery, and full chroma-based cut detection on the generated MKV |
| `test_gap_fill.py` | Synthetic harness | Rendered output with reference-gap fill, preamble/postamble trimming, lossless-sync overlap requirement |
| `test_path_selection.py` | Path selection | Routing: full-overlap linear -> lossless mux, unmatched-edge linear -> rendered trim |
| `test_real_media_matrix.py` | Fixture integration | Preamble-only, postamble-only, and preamble+postamble alignment on MKV-extracted audio (warp-map shape, segment boundaries, durations) |
| `test_cli_confidence_messages.py` | Helper math | Confidence-score formatting: normal reports, weak-match warnings, cut-detection spot-check cues |
| `test_subtitle_adjuster.py` | Synthetic harness | SRT per-cue timestamp remapping, bitmap subtitle constant-offset fallback |
| `test_directory_mode.py` | CLI behavior | Directory-mode file discovery, explicit two-file mode, single-argument rejection |
| `test_scenario_catalog.py` | Meta | Catalog completeness and `requires_real_media` flag correctness |

## Running the suite

```bash
# All tests (MKV fixture is auto-generated on first run)
uv run python -m unittest discover tests -v
```

27 tests, all passing. No tests are skipped when `ffmpeg` with `libx264` is available.

## Design principles

1. **Behavior over implementation detail.** Assert output duration, trimming, and mux-path selection -- not private intermediates.
2. **Every correction mode needs a scenario.** No-op, offset, edge trim, hole fill, and cut-detection fallback must each have explicit coverage.
3. **Synthetic fixtures carry the suite.** The auto-generated MKV uses deterministic pitched tones with known ground truth, making every test reproducible without external media files.
4. **Known limitations stay visible.** Hard cases are promoted to normal passing tests once fixed, not hidden behind `expectedFailure`.
