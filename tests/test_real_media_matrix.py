from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_harness import MOVIE_FIXTURE, ensure_movie_fixture
from frankenstein.sync.aligner import compute_warp_map
from frankenstein.sync.analyzer import extract_features


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for real-media matrix tests")
class RealMediaMatrixTests(unittest.TestCase):
    fixture = MOVIE_FIXTURE

    def test_fixture_with_preamble_trims_to_reference_duration(self) -> None:
        if not ensure_movie_fixture():
            self.skipTest("cannot generate fixture (ffmpeg + libx264 required)")

        warp_map = self._align_fixture_variant(
            reference_trim="10:70",
            selected_filter="[0:1]atrim=start=5:end=70,asetpts=PTS-STARTPTS[out]",
        )

        self.assertTrue(warp_map.is_linear_drift)
        self.assertEqual(len(warp_map.segments), 1)
        seg = warp_map.segments[0]
        self.assertAlmostEqual(seg.t_ref_start, 0.0, delta=0.7)
        self.assertAlmostEqual(seg.t_ref_end, 60.0, delta=0.7)
        self.assertAlmostEqual(seg.t_sel_start, 5.0, delta=1.0)
        self.assertAlmostEqual(seg.t_sel_end, 65.0, delta=1.0)
        self.assertAlmostEqual(warp_map.ref_duration, 60.0, delta=0.7)
        self.assertAlmostEqual(warp_map.sel_duration, 65.0, delta=0.7)

    def test_fixture_with_postamble_trims_to_reference_duration(self) -> None:
        if not ensure_movie_fixture():
            self.skipTest("cannot generate fixture (ffmpeg + libx264 required)")

        warp_map = self._align_fixture_variant(
            reference_trim="10:70",
            selected_filter="[0:1]atrim=start=10:end=75,asetpts=PTS-STARTPTS[out]",
        )

        self.assertTrue(warp_map.is_linear_drift)
        self.assertEqual(len(warp_map.segments), 1)
        seg = warp_map.segments[0]
        self.assertAlmostEqual(seg.t_ref_start, 0.0, delta=0.7)
        self.assertAlmostEqual(seg.t_ref_end, 60.0, delta=0.7)
        self.assertAlmostEqual(seg.t_sel_start, 0.0, delta=0.7)
        self.assertAlmostEqual(seg.t_sel_end, 60.0, delta=1.0)
        self.assertAlmostEqual(warp_map.ref_duration, 60.0, delta=0.7)
        self.assertAlmostEqual(warp_map.sel_duration, 65.0, delta=0.7)

    def test_fixture_with_preamble_and_postamble_trims_both_edges(self) -> None:
        if not ensure_movie_fixture():
            self.skipTest("cannot generate fixture (ffmpeg + libx264 required)")

        warp_map = self._align_fixture_variant(
            reference_trim="10:70",
            selected_filter="[0:1]atrim=start=5:end=75,asetpts=PTS-STARTPTS[out]",
        )

        self.assertTrue(warp_map.is_linear_drift)
        self.assertEqual(len(warp_map.segments), 1)
        seg = warp_map.segments[0]
        self.assertAlmostEqual(seg.t_ref_start, 0.0, delta=0.7)
        self.assertAlmostEqual(seg.t_ref_end, 60.0, delta=0.7)
        self.assertAlmostEqual(seg.t_sel_start, 5.0, delta=1.0)
        self.assertAlmostEqual(seg.t_sel_end, 65.0, delta=1.0)
        self.assertAlmostEqual(warp_map.ref_duration, 60.0, delta=0.7)
        self.assertAlmostEqual(warp_map.sel_duration, 70.0, delta=0.7)

    def _align_fixture_variant(self, reference_trim: str, selected_filter: str):
        with tempfile.TemporaryDirectory(prefix="frankensync-real-media-") as tmpdir:
            tmp = Path(tmpdir)
            reference_wav = tmp / "reference.wav"
            selected_wav = tmp / "selected.wav"

            ref_start, ref_end = reference_trim.split(":")
            self._run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(self.fixture),
                    "-map",
                    "0:1",
                    "-ss",
                    ref_start,
                    "-to",
                    ref_end,
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
                    str(self.fixture),
                    "-filter_complex",
                    selected_filter,
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

        return warp_map

    @staticmethod
    def _run(cmd: list[str]) -> None:
        subprocess.run(cmd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
