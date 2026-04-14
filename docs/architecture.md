# Frankenstein: scope, aims, and implementation notes

## 1. Scope and aims

`Frankenstein` is a practical MKV post-processing tool for a specific workflow:

- you have multiple releases of the **same episode or film**
- one file has the **best video**
- another file has the **preferred audio and/or subtitles**
- the tracks are **not directly mux-compatible in time**

The application's goal is to produce a new MKV that keeps the chosen video stream and aligns the chosen audio/subtitle streams onto that video's timeline.

### Core goals

1. **Preserve video quality**
   - video is stream-copied, not re-encoded

2. **Handle the two common sync problems**
   - **linear drift**: constant offset and/or gradual rate mismatch
   - **discrete cuts**: missing/censored scenes, added intros/outros, release differences

3. **Keep the UX simple**
   - interactive track selection through a TUI
   - minimal required inputs
   - useful defaults, including directory mode

4. **Prefer safer transformations**
   - use lossless timestamp correction when possible
   - only re-render audio when exact trimming/warping is required

### Explicit non-goals

At the moment, the project is **not** trying to be:

- a general media transcoder
- a batch automation/orchestration system
- a frame-perfect subtitle editor for bitmap subtitle formats
- a fully automatic solution for every real-world dub/remaster mismatch

In particular, real-world **cut detection** is still the weakest part of the system. Once a gap is detected, the renderer now handles it correctly; detecting every gap reliably is a separate problem.

---

## 2. User-facing workflow

The application currently supports two entry paths:

1. **Explicit mode**
   - `frankensync file1.mkv file2.mkv`

2. **Directory mode**
   - `frankensync`
   - scans the current directory for `.mkv` files
   - loads all discovered MKVs into the same TUI

After launch, the user selects:

1. video track
2. audio track
3. subtitle track (optional)

Then the pipeline:

1. extracts analysis WAVs
2. computes audio features
3. estimates alignment
4. decides between lossless sync or rendered audio correction
5. remaps subtitles if needed
6. muxes the final MKV

---

## 3. High-level architecture

The main code is organized by responsibility:

- `cli.py`
  - CLI entrypoint
  - orchestrates the whole pipeline
  - owns user-facing messages

- `tui/`
  - track selection UI
  - inspects input files and groups available streams

- `mkv/`
  - probing, extraction, and final muxing
  - thin wrappers around `ffprobe`, `ffmpeg`, `mkvextract`, and `mkvmerge`

- `sync/analyzer.py`
  - loads audio and extracts analysis features

- `sync/aligner.py`
  - computes the time relationship between selected and reference audio
  - outputs a `WarpMap`

- `sync/applier.py`
  - turns the `WarpMap` into an actual corrected audio stream

- `subtitle/adjuster.py`
  - remaps subtitle timestamps using the same timing model

- `models.py`
  - shared data structures (`Track`, `TrackSelection`, `WarpSegment`, `WarpMap`)

---

## 4. Timing model

The central abstraction is the **warp map**.

Each `WarpSegment` expresses a mapping between:

- a region of the **reference timeline**
- a region of the **selected audio timeline**

Conceptually:

```text
t_sel = slope * t_ref + intercept
```

where:

- `t_ref` is time on the chosen video/reference timeline
- `t_sel` is time on the chosen audio timeline

This model is useful because it supports both:

- a single linear segment for simple drift
- multiple segments for cut-based alignment

### Why not use a full DTW path directly?

A full frame-by-frame warp path is too detailed and too fragile for actual mux/render operations. The application reduces the alignment into a compact list of linear segments because:

- muxing tools reason better about offsets/rates than arbitrary warps
- subtitle remapping becomes manageable
- diagnostics stay understandable

---

## 5. Audio analysis and alignment

## 5.1 Feature extraction

Analysis happens on temporary mono WAVs at **16 kHz**.

The extracted signals are:

- **chroma CENS**
- **onset strength**

In practice, the current aligner relies mainly on chroma-based matching.

### Rationale

For dubbed content, speech transients often differ heavily across languages. Chroma and broader tonal content are more stable because:

- score/music is usually shared
- ambience and effects are often similar
- they are less language-dependent than raw onset peaks

## 5.2 Coarse-to-fine alignment

The aligner uses a two-stage process:

1. **global chroma cross-correlation**
   - gives a coarse starting offset

2. **chunked local alignment**
   - refines alignment in overlapping windows
   - produces anchor points `(t_ref, t_sel, confidence)`

These anchors are then:

- median-smoothed
- fit to a line when possible
- split into cut segments when offset jumps are large

### Rationale

This approach is cheaper and easier to reason about than running a heavy unrestricted DTW over a whole episode/movie.

It also gives the application a usable middle ground:

- simple cases stay simple
- obviously segmented cases can still be represented

## 5.3 Confidence

The reported confidence is a **feature-match strength indicator**, not a direct correctness guarantee.

That is why the CLI now uses language such as **weak feature match** instead of implying the output is automatically wrong.

### Rationale

In practice, a low match score can still lead to a correct no-op or simple correction, especially when the releases are already close. The warning is meant to encourage spot-checking, not to overstate failure.

---

## 6. Linear drift path

If anchors are well explained by a single line, the tool treats the problem as **linear drift**.

There are two sub-cases:

1. **Full-overlap linear drift**
   - selected and reference overlap edge-to-edge
   - handled with `mkvmerge --sync`
   - this is the preferred lossless path

2. **Linear drift with unmatched edges**
   - selected audio has extra leading/trailing content, or overlap does not cover both timelines fully
   - handled with the ffmpeg render path instead of `mkvmerge --sync`

### Rationale

`mkvmerge --sync` is ideal when only timestamps/rate need adjustment.

It is **not** the right tool when the audio must also be **trimmed** to remove:

- a leading preamble not present in the video/reference
- a trailing postamble not present in the video/reference

That is why the implementation explicitly checks whether the overlap is full before using the lossless path.

---

## 7. Cut / gap handling

When the warp contains multiple segments, the selected audio is not simply concatenated anymore.

The renderer now builds the output in **reference timeline order**:

- selected-audio pieces are trimmed and time-scaled into their mapped spans
- gaps in the reference timeline are filled from the **reference/original audio**

This is implemented in `sync/applier.py` via an intermediate `AudioPiece` plan.

### Rationale

This behavior is important for censored or shortened releases.

Without gap filling, the output would collapse missing scenes and drift out of sync after the first hole.

By rendering in reference time:

- the final audio duration matches the reference video timeline
- missing scenes can be preserved with original audio
- subtitle remapping remains conceptually aligned with the same reference timeline

---

## 8. Subtitle handling

Subtitle handling mirrors the audio timing model.

### Text subtitles (`.srt`, `.ass`, `.ssa`)

These are remapped with a piecewise-linear interpolator derived from the warp map.

### Bitmap subtitles (`PGS`, `VobSub`)

These cannot be fully retimed cue-by-cue in the current implementation.
Only a constant mux-time offset is applied.

### Rationale

Text subtitles expose timestamps directly and are cheap to remap.

Bitmap subtitles are much harder because meaningful retiming would require image-stream-level manipulation rather than simple text timestamp rewriting.

---

## 9. Muxing strategy

Final assembly uses `mkvmerge`.

The output strategy is:

- selected video track: stream-copy
- corrected audio:
  - either original track plus `--sync`
  - or a rendered ffmpeg output
- subtitle:
  - adjusted external file, or original stream

### Why mix `ffmpeg` and `mkvmerge`?

- `ffmpeg` is better at sample-accurate trimming, filter graphs, and audio rendering
- `mkvmerge` is better for final Matroska assembly and clean track muxing

The project intentionally uses each tool for what it is best at.

---

## 10. Design choices and rationales

### 10.1 Why a TUI instead of pure CLI flags?

Real media files often contain:

- multiple audio tracks
- many subtitle tracks
- poor or inconsistent metadata

A TUI is slower to automate, but much better for correct human selection in messy files.

### 10.2 Why preserve video untouched?

Re-encoding video would:

- increase runtime heavily
- reduce quality
- make the tool much more complex

The project is intentionally centered around **track replacement/alignment**, not video transcoding.

### 10.3 Why prefer lossless sync first?

Whenever timestamps alone solve the problem, that is the cleanest outcome:

- fastest path
- no generational audio loss
- simplest mux

But the implementation now explicitly falls back to ffmpeg rendering when trimming or gap construction is required.

### 10.4 Why validate segments aggressively?

Real-world noisy matches can produce absurd speed factors or tiny bogus segments.

If those are fed blindly into `atempo`, the result can be catastrophically wrong. The code therefore rejects suspicious segments instead of trying to "make something work" from clearly bad anchors.

---

## 11. Known limitations

The biggest current limitation is **detecting cuts reliably in hard real-world cases**.

More specifically:

- weakly matched dubs can still collapse to linear drift when they should become segmented
- bitmap subtitles only support constant offset, not full remapping
- the confidence metric is heuristic, not a calibrated probability of correctness

The repository already contains tests documenting that real-media cut detection is still incomplete.

---

## 12. Future directions

Useful next improvements would include:

1. stronger real-world cut detection
2. better diagnostics for why a file fell back to linear drift
3. optional saved alignment reports
4. more fixture-backed integration tests across multiple release patterns
5. clearer UI signaling when ffmpeg rendering is chosen over lossless sync

---

## 13. Summary

`Frankenstein` is a focused MKV synchronization tool, not a general media suite.

Its design priorities are:

- keep the chosen video untouched
- infer a practical timing model between releases
- use the lightest safe correction path available
- preserve the reference timeline when cuts or censored gaps are involved

That trade-off keeps the tool usable on real files while leaving room to improve the hardest part: robust automatic cut detection.
