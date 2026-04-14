from __future__ import annotations

import unittest

import numpy as np

from film_tracks_aligner.mkv.muxer import _build_linear_sync
from film_tracks_aligner.sync.aligner import (
    _global_xcorr_chroma,
    _linear_drift_fallback,
    _make_linear_warp,
)


class LinearAlignmentTests(unittest.TestCase):
    def test_global_xcorr_positive_offset_means_selected_starts_later(self) -> None:
        chroma_ref = np.zeros((2, 16), dtype=float)
        chroma_sel = np.zeros((2, 16), dtype=float)
        pattern = np.array([0.0, 1.0, 2.0, 1.0, 0.0])

        chroma_ref[0, 3:8] = pattern
        chroma_ref[1, 6:11] = pattern[::-1]

        chroma_sel[0, 6:11] = pattern
        chroma_sel[1, 9:14] = pattern[::-1]

        offset_sec, confidence = _global_xcorr_chroma(
            chroma_ref,
            chroma_sel,
            fps=1.0,
            downsample=1,
        )

        self.assertAlmostEqual(offset_sec, 3.0)
        self.assertGreaterEqual(confidence, 0.0)

    def test_positive_offset_builds_negative_delay(self) -> None:
        warp_map = _make_linear_warp(
            ref_duration=120.0,
            sel_duration=130.0,
            offset_sec=5.0,
        )

        segment = warp_map.segments[0]
        self.assertAlmostEqual(segment.t_ref_start, 0.0)
        self.assertAlmostEqual(segment.t_sel_start, 5.0)
        self.assertEqual(_build_linear_sync(warp_map, audio_track_id=1), "1:-5000,1/1")

    def test_negative_offset_preserves_constant_delay(self) -> None:
        warp_map = _make_linear_warp(
            ref_duration=120.0,
            sel_duration=120.0,
            offset_sec=-5.0,
        )

        segment = warp_map.segments[0]
        self.assertAlmostEqual(segment.t_ref_start, 5.0)
        self.assertAlmostEqual(segment.t_ref_end, 120.0)
        self.assertAlmostEqual(segment.t_sel_start, 0.0)
        self.assertAlmostEqual(segment.t_sel_end, 115.0)
        self.assertEqual(_build_linear_sync(warp_map, audio_track_id=1), "1:5000,1/1")

    def test_linear_fit_keeps_negative_intercept(self) -> None:
        anchors = [
            (5.0, 0.0, 0.9),
            (35.0, 30.0, 0.9),
            (65.0, 60.0, 0.9),
        ]

        warp_map = _linear_drift_fallback(
            anchors=anchors,
            ref_duration=120.0,
            sel_duration=120.0,
            global_offset_sec=-5.0,
        )

        self.assertIsNotNone(warp_map)
        segment = warp_map.segments[0]
        self.assertAlmostEqual(segment.t_ref_start, 5.0)
        self.assertAlmostEqual(segment.t_sel_start, 0.0)
        self.assertEqual(_build_linear_sync(warp_map, audio_track_id=1), "1:5000,1/1")


if __name__ == "__main__":
    unittest.main()
