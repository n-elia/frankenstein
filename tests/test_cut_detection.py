from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_harness import MOVIE_FIXTURE, ensure_movie_fixture
from frankenstein.sync.aligner import (
    _anchors_to_warp_map,
    _build_cut_warp_from_edge_offsets,
    compute_warp_map,
)
from frankenstein.sync.analyzer import extract_features


class CutDetectionUnitTests(unittest.TestCase):
    def test_anchor_cut_becomes_reference_gap(self) -> None:
        warp_map = _anchors_to_warp_map(
            anchors=[
                (0.0, 0.0, 0.9),
                (10.0, 10.0, 0.9),
                (20.0, 20.0, 0.9),
                (30.0, 20.0, 0.9),
                (40.0, 30.0, 0.9),
                (50.0, 40.0, 0.9),
            ],
            ref_duration=60.0,
            sel_duration=50.0,
        )

        self.assertFalse(warp_map.is_linear_drift)
        self.assertEqual(len(warp_map.segments), 2)

        first, second = warp_map.segments
        self.assertAlmostEqual(first.t_ref_start, 0.0)
        self.assertAlmostEqual(first.t_ref_end, 20.0)
        self.assertAlmostEqual(first.t_sel_start, 0.0)
        self.assertAlmostEqual(first.t_sel_end, 20.0)

        self.assertAlmostEqual(second.t_ref_start, 30.0)
        self.assertAlmostEqual(second.t_sel_start, 20.0)
        self.assertAlmostEqual(second.t_ref_end, 60.0)
        self.assertAlmostEqual(second.t_sel_end, 50.0)

    def test_edge_offset_recovery_builds_reference_gap(self) -> None:
        warp_map = _build_cut_warp_from_edge_offsets(
            ref_duration=90.0,
            sel_duration=75.0,
            start_offset=0.0,
            end_offset=-15.0,
        )

        self.assertIsNotNone(warp_map)
        assert warp_map is not None
        self.assertFalse(warp_map.is_linear_drift)
        self.assertEqual(len(warp_map.segments), 2)

        first, second = warp_map.segments
        ref_gap = second.t_ref_start - first.t_ref_end
        self.assertGreaterEqual(ref_gap, 5.0)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for fixture-backed cut tests")
class FixtureCutDetectionTests(unittest.TestCase):
    def test_censored_fixture_audio_detects_cut(self) -> None:
        if not ensure_movie_fixture():
            self.skipTest("cannot generate fixture (ffmpeg + libx264 required)")
        fixture = MOVIE_FIXTURE

        with tempfile.TemporaryDirectory(prefix="frankensync-cut-test-") as tmpdir:
            tmp = Path(tmpdir)
            reference_wav = tmp / "reference.wav"
            selected_wav = tmp / "selected.wav"

            self._run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(fixture),
                    "-map",
                    "0:1",
                    "-ss",
                    "0",
                    "-t",
                    "90",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(reference_wav),
                ]
            )

            self._run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(fixture),
                    "-filter_complex",
                    (
                        "[0:1]atrim=start=0:end=30,asetpts=PTS-STARTPTS[a0];"
                        "[0:1]atrim=start=45:end=90,asetpts=PTS-STARTPTS[a1];"
                        "[a0][a1]concat=n=2:v=0:a=1[out]"
                    ),
                    "-map",
                    "[out]",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(selected_wav),
                ]
            )

            chroma_ref, onset_ref, ref_dur = extract_features(reference_wav)
            chroma_sel, onset_sel, sel_dur = extract_features(selected_wav)

            with patch("frankenstein.sync.aligner._CHUNK_SEC", 15.0), patch(
                "frankenstein.sync.aligner._STEP_SEC", 5.0
            ), patch("frankenstein.sync.aligner._SEARCH_SEC", 12.0), patch(
                "frankenstein.sync.aligner._MIN_SEGMENT_SEC", 5.0
            ):
                warp_map, _ = compute_warp_map(
                    chroma_ref,
                    chroma_sel,
                    onset_ref,
                    onset_sel,
                    ref_dur,
                    sel_dur,
                )

        self.assertFalse(warp_map.is_linear_drift)
        self.assertGreaterEqual(len(warp_map.segments), 2)

        segment_gaps = [
            (
                curr.t_ref_start - prev.t_ref_end,
                curr.t_sel_start - prev.t_sel_end,
            )
            for prev, curr in zip(warp_map.segments, warp_map.segments[1:])
        ]
        self.assertTrue(
            any(ref_gap >= 10.0 and abs(sel_gap) <= 2.0 for ref_gap, sel_gap in segment_gaps),
            segment_gaps,
        )

    @staticmethod
    def _run(cmd: list[str]) -> None:
        subprocess.run(cmd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
