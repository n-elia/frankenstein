from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.signal import correlate, correlation_lags

from frankenstein.models import WarpMap, WarpSegment
from frankenstein.sync.analyzer import SEC_PER_FRAME

# ---------------------------------------------------------------------------
# Tuning parameters
# ---------------------------------------------------------------------------

# Chunk size for local alignment (seconds).
_CHUNK_SEC = 120.0

# Step between consecutive chunks (50% overlap → smoother map).
_STEP_SEC = 60.0

# Half-width of the search window around the expected position (seconds).
_SEARCH_SEC = 45.0

# Minimum normalised cross-correlation value to accept a chunk match.
_MIN_CHUNK_CONFIDENCE = 0.05

# A jump larger than this between consecutive smoothed offsets marks a cut.
_CUT_JUMP_SEC = 5.0

# Median-filter window (in anchors) for smoothing offsets. Must be odd.
_SMOOTH_WINDOW = 5

# If residual std of offsets around linear fit is below this, treat as
# pure linear drift.
_LINEAR_RESIDUAL_SEC = 1.5

# Hard bounds on per-segment speed factor.
_MAX_SEGMENT_SPEED = 1.5
_MIN_SEGMENT_SPEED = 1.0 / _MAX_SEGMENT_SPEED

# Segments shorter than this (in seconds) are unreliable.
_MIN_SEGMENT_SEC = 30.0

# Minimum confidence to report to the user.
MIN_CONFIDENCE = 0.4


def compute_warp_map(
    chroma_ref: np.ndarray,
    chroma_sel: np.ndarray,
    onset_ref: np.ndarray,
    onset_sel: np.ndarray,
    ref_duration: float,
    sel_duration: float,
    progress_cb: Callable[[str], None] | None = None,
    on_chunk: Callable[[int, int, tuple[float, float, float] | None], None] | None = None,
) -> tuple[WarpMap, float]:
    """Compute a time warp map using chunked cross-correlation on chroma.

    The alignment uses **chroma features** (tonal / musical content) as the
    primary signal. This is robust across different-language dubs of the same
    film because the soundtrack and sound effects are shared. Onset strength
    (transient energy) is unreliable for dubbed audio — different languages
    produce completely different speech transients.

    Algorithm
    ---------
    1. Global chroma cross-correlation (downsampled) → coarse offset.
    2. Chunked chroma cross-correlation → fine anchor points.
    3. Median-filter anchor offsets to reject per-chunk outliers.
    4. Fit a line; if residuals are small → linear drift → single segment.
    5. Otherwise, detect cuts as jumps in the smoothed offset series.
    6. Validate every segment for sanity; fall back on failure.
    """
    fps = 1.0 / SEC_PER_FRAME

    chunk_frames = max(1, int(_CHUNK_SEC * fps))
    step_frames  = max(1, int(_STEP_SEC  * fps))

    # --- Phase 1: coarse whole-file cross-correlation on chroma ---
    if progress_cb:
        progress_cb("Global chroma cross-correlation…")

    global_offset_sec, coarse_conf = _global_xcorr_chroma(
        chroma_ref, chroma_sel, fps,
    )

    if progress_cb:
        progress_cb(
            f"Global offset: {global_offset_sec:+.2f}s  "
            f"(coarse confidence: {coarse_conf:.2f})"
        )

    # --- Phase 2: chunked chroma alignment ---
    if progress_cb:
        n_chunks = max(1, (chroma_ref.shape[1] - chunk_frames) // step_frames + 1)
        progress_cb(f"Chunked chroma alignment ({n_chunks} chunks × {_CHUNK_SEC:.0f}s)…")

    anchors = _chunked_alignment_chroma(
        chroma_ref, chroma_sel, fps,
        global_offset_sec, chunk_frames, step_frames,
        on_chunk=on_chunk,
    )

    if progress_cb:
        progress_cb(f"Collected {len(anchors)} anchor points")

    # --- Phase 3: smooth anchor offsets ---
    anchors = _smooth_anchors(anchors)

    # --- Phase 4: sparse-anchor cut recovery ---
    cut_rescue = _recover_cut_from_edges(
        chroma_ref=chroma_ref,
        chroma_sel=chroma_sel,
        fps=fps,
        anchors=anchors,
        ref_duration=ref_duration,
        sel_duration=sel_duration,
    )
    if cut_rescue is not None:
        warp_map = cut_rescue
        if progress_cb:
            progress_cb(
                "Recovered non-linear warp from sparse anchors "
                "using edge-offset estimation"
            )
    else:
        # --- Phase 5: linear drift fallback ---
        linear_fallback = _linear_drift_fallback(
            anchors, ref_duration, sel_duration, global_offset_sec,
        )
        if linear_fallback is not None:
            warp_map = linear_fallback
            if progress_cb and warp_map.segments:
                factor = warp_map.segments[0].speed_factor
                offset = warp_map.segments[0].t_sel_start
                progress_cb(
                    f"Warp resolves to linear drift "
                    f"(speed {factor:.6f}, offset {offset:+.2f}s)"
                )
        else:
            # --- Phase 6: segmented warp ---
            warp_map = _anchors_to_warp_map(anchors, ref_duration, sel_duration)

            # --- Phase 7: validate ---
            if not _segments_are_sane(warp_map.segments):
                if progress_cb:
                    progress_cb(
                        "Warning: segmented warp failed sanity checks — "
                        "falling back to global linear drift"
                    )
                warp_map = _make_linear_warp(ref_duration, sel_duration, global_offset_sec)

    # --- Confidence ---
    confidences = [c for _, _, c in anchors] if anchors else []
    confidence = float(np.clip(
        np.mean(confidences) if confidences else coarse_conf, 0.0, 1.0,
    ))

    if progress_cb:
        if warp_map.is_linear_drift:
            mode = "linear drift"
        else:
            mode = f"{len(warp_map.segments)} segments (cuts detected)"
        progress_cb(f"Alignment complete — {mode}  confidence: {confidence:.2f}")

    return warp_map, confidence


# ---------------------------------------------------------------------------
# Chroma-based cross-correlation
# ---------------------------------------------------------------------------

def _norm_bands(x: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-std normalise each row (band) of a 2-D array."""
    out = np.empty_like(x, dtype=np.float64)
    for b in range(x.shape[0]):
        row = x[b].astype(np.float64)
        row = row - row.mean()
        std = row.std()
        out[b] = row / std if std > 1e-8 else row
    return out


def _multiband_xcorr(
    a: np.ndarray,
    b: np.ndarray,
    mode: str = "full",
) -> np.ndarray:
    """Sum of per-band cross-correlations between two (B, T) arrays.

    Returns a 1-D correlation array whose peak indicates the best-matching
    lag between *a* and *b*. This captures tonal/musical similarities
    (shared across dubs) while being insensitive to language-specific
    speech transients.
    """
    assert a.shape[0] == b.shape[0], "band count mismatch"
    corr = None
    for band in range(a.shape[0]):
        c = correlate(a[band], b[band], mode=mode, method="fft")
        corr = c if corr is None else corr + c
    return corr  # type: ignore[return-value]


def _global_xcorr_chroma(
    chroma_ref: np.ndarray,
    chroma_sel: np.ndarray,
    fps: float,
    downsample: int = 8,
) -> tuple[float, float]:
    """Global offset from downsampled multi-band chroma cross-correlation.

    Returned offset follows the warp-map convention ``t_sel = t_ref + offset``:
    positive values mean the selected audio starts later than the reference
    (e.g. selected has a leading preamble that must be trimmed).
    """
    ref_ds = _norm_bands(chroma_ref[:, ::downsample])
    sel_ds = _norm_bands(chroma_sel[:, ::downsample])

    corr = _multiband_xcorr(ref_ds, sel_ds, mode="full")
    lags = correlation_lags(ref_ds.shape[1], sel_ds.shape[1], mode="full")

    best = int(np.argmax(corr))
    offset_frames = lags[best] * downsample
    offset_sec = float(-offset_frames) / fps

    peak = float(corr[best])
    noise = float(np.std(corr))
    n_bands = chroma_ref.shape[0]
    confidence = float(np.clip(
        peak / (noise * np.sqrt(ref_ds.shape[1]) * n_bands + 1e-8),
        0.0, 1.0,
    ))

    return offset_sec, confidence


def _chunked_alignment_chroma(
    chroma_ref: np.ndarray,
    chroma_sel: np.ndarray,
    fps: float,
    initial_offset_sec: float,
    chunk_frames: int,
    step_frames: int,
    on_chunk: Callable[[int, int, tuple[float, float, float] | None], None] | None = None,
) -> list[tuple[float, float, float]]:
    """Slide a chroma chunk over the reference and return anchor points.

    Returns list of (t_ref, t_sel, confidence) tuples.
    If *on_chunk* is provided it is called once per chunk iteration with
    ``(chunk_idx, n_chunks, anchor_or_none)``.
    """
    n_ref = chroma_ref.shape[1]
    n_sel = chroma_sel.shape[1]
    n_bands = chroma_ref.shape[0]

    anchors: list[tuple[float, float, float]] = []
    running_offset_sec = initial_offset_sec
    n_chunks = max(1, (n_ref - chunk_frames) // step_frames + 1)
    chunk_idx = 0

    chunk_start = 0
    while chunk_start + chunk_frames <= n_ref:
        t_ref = chunk_start / fps
        chunk = _norm_bands(chroma_ref[:, chunk_start : chunk_start + chunk_frames])
        anchor: tuple[float, float, float] | None = None

        # Search region in sel around expected position
        expected_sel = t_ref + running_offset_sec
        search_lo = max(0, int((expected_sel - _SEARCH_SEC) * fps))
        search_hi = min(n_sel, int((expected_sel + _SEARCH_SEC) * fps) + chunk_frames)

        if search_hi - search_lo >= chunk_frames // 4:
            region = _norm_bands(chroma_sel[:, search_lo:search_hi])
            corr = _multiband_xcorr(chunk, region, mode="valid")

            if corr is not None and len(corr) > 0:
                best_idx = int(np.argmax(corr))
                peak = float(corr[best_idx])
                noise = float(np.std(corr))
                chunk_conf = float(np.clip(
                    peak / (noise * np.sqrt(chunk_frames) * n_bands + 1e-8),
                    0.0, 1.0,
                ))

                if chunk_conf >= _MIN_CHUNK_CONFIDENCE:
                    t_sel = (search_lo + best_idx) / fps
                    running_offset_sec = t_sel - t_ref
                    anchor = (t_ref, t_sel, chunk_conf)
                    anchors.append(anchor)

        if on_chunk:
            on_chunk(chunk_idx, n_chunks, anchor)
        chunk_idx += 1
        chunk_start += step_frames

    return anchors


# ---------------------------------------------------------------------------
# Anchor post-processing and warp map construction
# ---------------------------------------------------------------------------

def _smooth_anchors(
    anchors: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """Median-filter the offset series to reject outliers."""
    if len(anchors) < 3:
        return anchors

    t_refs = np.array([a[0] for a in anchors])
    offsets = np.array([a[1] - a[0] for a in anchors])
    confs  = np.array([a[2] for a in anchors])

    half = _SMOOTH_WINDOW // 2
    smoothed = np.empty_like(offsets)
    for i in range(len(offsets)):
        lo = max(0, i - half)
        hi = min(len(offsets), i + half + 1)
        smoothed[i] = float(np.median(offsets[lo:hi]))

    return [
        (float(t_refs[i]), float(t_refs[i] + smoothed[i]), float(confs[i]))
        for i in range(len(anchors))
    ]


def _linear_drift_fallback(
    anchors: list[tuple[float, float, float]],
    ref_duration: float,
    sel_duration: float,
    global_offset_sec: float,
) -> WarpMap | None:
    """If anchors are well-explained by a single linear fit, return that warp."""
    if not anchors:
        return _make_linear_warp(ref_duration, sel_duration, global_offset_sec)
    if len(anchors) < 3:
        return _make_linear_warp(ref_duration, sel_duration, global_offset_sec)

    t_refs = np.array([a[0] for a in anchors])
    t_sels = np.array([a[1] for a in anchors])

    slope, intercept = np.polyfit(t_refs, t_sels, 1)
    predicted = slope * t_refs + intercept
    residual_std = float(np.std(t_sels - predicted))

    if residual_std <= _LINEAR_RESIDUAL_SEC:
        seg = _build_linear_overlap_segment(
            ref_duration=ref_duration,
            sel_duration=sel_duration,
            slope=float(slope),
            intercept=float(intercept),
        )
        if seg is None:
            return _make_linear_warp(ref_duration, sel_duration, global_offset_sec)
        if not (_MIN_SEGMENT_SPEED <= seg.speed_factor <= _MAX_SEGMENT_SPEED):
            return _make_linear_warp(ref_duration, sel_duration, global_offset_sec)

        return WarpMap(segments=[seg], ref_duration=ref_duration, sel_duration=sel_duration)

    return None


def _make_linear_warp(
    ref_duration: float,
    sel_duration: float,
    offset_sec: float,
) -> WarpMap:
    """Build a single-segment linear warp using a constant global offset."""
    seg = _build_linear_overlap_segment(
        ref_duration=ref_duration,
        sel_duration=sel_duration,
        slope=1.0,
        intercept=offset_sec,
    )
    if seg is None:
        seg = WarpSegment(0.0, ref_duration, 0.0, sel_duration)
    return WarpMap(
        segments=[seg],
        ref_duration=ref_duration,
        sel_duration=sel_duration,
    )


def _segments_are_sane(segments: list[WarpSegment]) -> bool:
    """Reject segmented warps whose pieces have absurd speed factors or lengths."""
    if not segments:
        return False
    for seg in segments:
        ref_dur = seg.t_ref_end - seg.t_ref_start
        sel_dur = seg.t_sel_end - seg.t_sel_start
        if ref_dur <= 0 or sel_dur <= 0:
            continue
        if ref_dur < _MIN_SEGMENT_SEC:
            return False
        speed = sel_dur / ref_dur
        if not (_MIN_SEGMENT_SPEED <= speed <= _MAX_SEGMENT_SPEED):
            return False
    return any(
        (s.t_ref_end - s.t_ref_start) > 0 and (s.t_sel_end - s.t_sel_start) > 0
        for s in segments
    )


def _anchors_to_warp_map(
    anchors: list[tuple[float, float, float]],
    ref_duration: float,
    sel_duration: float,
) -> WarpMap:
    """Convert anchor points to a WarpMap, detecting cuts as offset jumps."""
    if len(anchors) == 1:
        t_ref, t_sel, _ = anchors[0]
        offset = t_sel - t_ref
        seg = _build_linear_overlap_segment(
            ref_duration=ref_duration,
            sel_duration=sel_duration,
            slope=1.0,
            intercept=offset,
        )
        if seg is None:
            seg = WarpSegment(0.0, ref_duration, 0.0, sel_duration)
        return WarpMap(
            segments=[seg],
            ref_duration=ref_duration,
            sel_duration=sel_duration,
        )

    segments: list[WarpSegment] = []
    seg_t_ref_start = anchors[0][0]
    seg_t_sel_start = anchors[0][1]
    prev_offset = anchors[0][1] - anchors[0][0]

    for i in range(1, len(anchors)):
        t_ref, t_sel, _ = anchors[i]
        offset = t_sel - t_ref
        offset_jump = abs(offset - prev_offset)

        if offset_jump > _CUT_JUMP_SEC:
            prev_t_ref, prev_t_sel, _ = anchors[i - 1]
            if prev_t_ref > seg_t_ref_start and prev_t_sel > seg_t_sel_start:
                segments.append(WarpSegment(
                    t_ref_start=float(seg_t_ref_start),
                    t_ref_end=float(prev_t_ref),
                    t_sel_start=float(seg_t_sel_start),
                    t_sel_end=float(prev_t_sel),
                ))
            seg_t_ref_start = t_ref
            seg_t_sel_start = t_sel

        prev_offset = offset

    segments.append(WarpSegment(
        t_ref_start=float(seg_t_ref_start),
        t_ref_end=ref_duration,
        t_sel_start=float(seg_t_sel_start),
        t_sel_end=sel_duration,
    ))

    return WarpMap(segments=segments, ref_duration=ref_duration, sel_duration=sel_duration)


def _recover_cut_from_edges(
    chroma_ref: np.ndarray,
    chroma_sel: np.ndarray,
    fps: float,
    anchors: list[tuple[float, float, float]],
    ref_duration: float,
    sel_duration: float,
) -> WarpMap | None:
    """Recover a two-segment cut map when chunk anchors are too sparse."""
    if len(anchors) >= 3:
        return None

    window_sec = float(np.clip(_CHUNK_SEC, 10.0, 30.0))
    search_sec = max(20.0, _SEARCH_SEC * 2.0)

    start = _estimate_edge_offset(
        chroma_ref=chroma_ref,
        chroma_sel=chroma_sel,
        fps=fps,
        window_sec=window_sec,
        search_sec=search_sec,
        at_end=False,
    )
    end = _estimate_edge_offset(
        chroma_ref=chroma_ref,
        chroma_sel=chroma_sel,
        fps=fps,
        window_sec=window_sec,
        search_sec=search_sec,
        at_end=True,
    )
    if start is None or end is None:
        return None

    _, start_offset, start_conf = start
    _, end_offset, end_conf = end
    if min(start_conf, end_conf) < 0.01:
        return None
    if (start_offset - end_offset) <= _CUT_JUMP_SEC:
        return None

    return _build_cut_warp_from_edge_offsets(
        ref_duration=ref_duration,
        sel_duration=sel_duration,
        start_offset=start_offset,
        end_offset=end_offset,
    )


def _estimate_edge_offset(
    chroma_ref: np.ndarray,
    chroma_sel: np.ndarray,
    fps: float,
    window_sec: float,
    search_sec: float,
    at_end: bool,
) -> tuple[float, float, float] | None:
    """Estimate offset near the start or end via local multi-band xcorr."""
    n_ref = chroma_ref.shape[1]
    n_sel = chroma_sel.shape[1]
    n_bands = chroma_ref.shape[0]

    window_frames = max(1, int(window_sec * fps))
    search_frames = max(window_frames, int(search_sec * fps))
    if n_ref < window_frames or n_sel < window_frames:
        return None

    if at_end:
        ref_start = n_ref - window_frames
        sel_start = max(0, n_sel - (window_frames + search_frames))
        sel_end = n_sel
    else:
        ref_start = 0
        sel_start = 0
        sel_end = min(n_sel, window_frames + search_frames)

    ref_slice = _norm_bands(chroma_ref[:, ref_start : ref_start + window_frames])
    sel_slice = _norm_bands(chroma_sel[:, sel_start:sel_end])
    if sel_slice.shape[1] < window_frames // 2:
        return None

    corr = _multiband_xcorr(ref_slice, sel_slice, mode="full")
    lags = correlation_lags(ref_slice.shape[1], sel_slice.shape[1], mode="full")

    max_shift_frames = max(1, int(search_sec * fps))
    shift_frames = -lags
    valid = np.abs(shift_frames) <= max_shift_frames
    if not np.any(valid):
        return None

    masked = np.where(valid, corr, -np.inf)
    best = int(np.argmax(masked))
    t_ref = ref_start / fps
    t_sel = (sel_start - lags[best]) / fps
    offset = t_sel - t_ref

    peak = float(corr[best])
    noise = float(np.std(corr))
    confidence = float(np.clip(
        peak / (noise * np.sqrt(ref_slice.shape[1]) * n_bands + 1e-8),
        0.0,
        1.0,
    ))
    return float(t_ref), float(offset), confidence


def _build_cut_warp_from_edge_offsets(
    ref_duration: float,
    sel_duration: float,
    start_offset: float,
    end_offset: float,
) -> WarpMap | None:
    """Build a two-segment warp map from differing start/end offsets."""
    ref_gap = start_offset - end_offset
    if ref_gap <= _CUT_JUMP_SEC:
        return None

    start_overlap = _build_linear_overlap_segment(
        ref_duration=ref_duration,
        sel_duration=sel_duration,
        slope=1.0,
        intercept=start_offset,
    )
    end_overlap = _build_linear_overlap_segment(
        ref_duration=ref_duration,
        sel_duration=sel_duration,
        slope=1.0,
        intercept=end_offset,
    )
    if start_overlap is None or end_overlap is None:
        return None

    split_ref_1_lo = max(start_overlap.t_ref_start, end_overlap.t_ref_start - ref_gap)
    split_ref_1_hi = min(start_overlap.t_ref_end, end_overlap.t_ref_end - ref_gap)
    if split_ref_1_hi <= split_ref_1_lo:
        return None

    preferred = 0.5 * (split_ref_1_lo + split_ref_1_hi)
    split_ref_1 = float(np.clip(preferred, split_ref_1_lo, split_ref_1_hi))
    split_ref_2 = split_ref_1 + ref_gap

    seg1 = WarpSegment(
        t_ref_start=float(start_overlap.t_ref_start),
        t_ref_end=split_ref_1,
        t_sel_start=float(start_overlap.t_ref_start + start_offset),
        t_sel_end=float(split_ref_1 + start_offset),
    )
    seg2 = WarpSegment(
        t_ref_start=float(split_ref_2),
        t_ref_end=float(end_overlap.t_ref_end),
        t_sel_start=float(split_ref_2 + end_offset),
        t_sel_end=float(end_overlap.t_ref_end + end_offset),
    )
    if not _segments_are_sane([seg1, seg2]):
        return None

    return WarpMap(
        segments=[seg1, seg2],
        ref_duration=ref_duration,
        sel_duration=sel_duration,
    )


def _build_linear_overlap_segment(
    ref_duration: float,
    sel_duration: float,
    slope: float,
    intercept: float,
) -> WarpSegment | None:
    """Build the overlapping linear segment for ``t_sel = slope * t_ref + intercept``."""
    if slope <= 0:
        return None

    t_ref_start = max(0.0, -intercept / slope)
    t_ref_end = min(ref_duration, (sel_duration - intercept) / slope)
    if t_ref_end <= t_ref_start:
        return None

    t_sel_start = float(np.clip(slope * t_ref_start + intercept, 0.0, sel_duration))
    t_sel_end = float(np.clip(slope * t_ref_end + intercept, 0.0, sel_duration))
    if t_sel_end <= t_sel_start:
        return None

    return WarpSegment(
        t_ref_start=float(t_ref_start),
        t_ref_end=float(t_ref_end),
        t_sel_start=t_sel_start,
        t_sel_end=t_sel_end,
    )
