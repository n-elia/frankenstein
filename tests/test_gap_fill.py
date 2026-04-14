from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import soundfile as sf

from audio_harness import dominant_frequency, make_pcm_track, write_tone_sequence
from film_tracks_aligner.models import WarpMap, WarpSegment
from film_tracks_aligner.sync.applier import _build_audio_pieces, _can_use_lossless_sync, apply_warp


class GapFillTests(unittest.TestCase):
    def test_lossless_sync_requires_full_overlap(self) -> None:
        self.assertTrue(
            _can_use_lossless_sync(
                WarpMap(
                    segments=[WarpSegment(0.0, 4.0, 0.0, 4.0)],
                    ref_duration=4.0,
                    sel_duration=4.0,
                )
            )
        )
        self.assertFalse(
            _can_use_lossless_sync(
                WarpMap(
                    segments=[WarpSegment(0.0, 4.0, 1.0, 5.0)],
                    ref_duration=4.0,
                    sel_duration=6.0,
                )
            )
        )

    def test_build_audio_pieces_inserts_reference_holes(self) -> None:
        pieces = _build_audio_pieces(
            [
                WarpSegment(0.0, 10.0, 0.0, 10.0),
                WarpSegment(20.0, 40.0, 10.0, 30.0),
            ],
            ref_duration=50.0,
        )

        self.assertEqual(
            [(piece.source, piece.src_start, piece.src_end) for piece in pieces],
            [
                ("selected", 0.0, 10.0),
                ("reference", 10.0, 20.0),
                ("selected", 10.0, 30.0),
                ("reference", 40.0, 50.0),
            ],
        )

    def test_apply_warp_fills_reference_gap(self) -> None:
        sample_rate = 16000
        warp_map = WarpMap(
            segments=[
                WarpSegment(0.0, 2.0, 0.0, 2.0),
                WarpSegment(4.0, 6.0, 2.0, 4.0),
            ],
            ref_duration=6.0,
            sel_duration=4.0,
        )

        with tempfile.TemporaryDirectory(prefix="frankensync-gap-fill-") as tmpdir:
            tmp = Path(tmpdir)
            reference_path = tmp / "reference.wav"
            selected_path = tmp / "selected.wav"
            write_tone_sequence(
                reference_path,
                [(440.0, 2.0), (880.0, 2.0), (660.0, 2.0)],
                sample_rate=sample_rate,
            )
            write_tone_sequence(
                selected_path,
                [(440.0, 2.0), (660.0, 2.0)],
                sample_rate=sample_rate,
            )

            selected_track = make_pcm_track(selected_path, sample_rate=sample_rate)
            reference_track = make_pcm_track(reference_path, sample_rate=sample_rate)

            warped = apply_warp(selected_track, reference_track, warp_map, tmp)
            self.assertIsNotNone(warped)
            warped_path, delay_ms = warped

            self.assertEqual(delay_ms, 0)
            rendered_audio, rendered_sr = sf.read(warped_path)

        self.assertEqual(rendered_sr, sample_rate)
        self.assertAlmostEqual(len(rendered_audio) / rendered_sr, 6.0, places=2)
        middle = rendered_audio[2 * sample_rate : 4 * sample_rate]
        self.assertAlmostEqual(dominant_frequency(middle, rendered_sr), 880.0, delta=20.0)

    def test_apply_warp_trims_linear_preamble_and_postamble(self) -> None:
        sample_rate = 16000

        warp_map = WarpMap(
            segments=[WarpSegment(0.0, 4.0, 1.0, 5.0)],
            ref_duration=4.0,
            sel_duration=6.0,
        )

        with tempfile.TemporaryDirectory(prefix="frankensync-edge-trim-") as tmpdir:
            tmp = Path(tmpdir)
            reference_path = tmp / "reference.wav"
            selected_path = tmp / "selected.wav"
            write_tone_sequence(
                reference_path,
                [(440.0, 2.0), (660.0, 2.0)],
                sample_rate=sample_rate,
            )
            write_tone_sequence(
                selected_path,
                [(220.0, 1.0), (440.0, 2.0), (660.0, 2.0), (330.0, 1.0)],
                sample_rate=sample_rate,
            )

            selected_track = make_pcm_track(selected_path, sample_rate=sample_rate)
            reference_track = make_pcm_track(reference_path, sample_rate=sample_rate)

            warped = apply_warp(selected_track, reference_track, warp_map, tmp)
            self.assertIsNotNone(warped)
            warped_path, delay_ms = warped

            self.assertEqual(delay_ms, 0)
            rendered_audio, rendered_sr = sf.read(warped_path)

        self.assertEqual(rendered_sr, sample_rate)
        self.assertAlmostEqual(len(rendered_audio) / rendered_sr, 4.0, places=2)
        opening = rendered_audio[: 2 * sample_rate]
        ending = rendered_audio[2 * sample_rate :]
        self.assertAlmostEqual(dominant_frequency(opening, rendered_sr), 440.0, delta=20.0)
        self.assertAlmostEqual(dominant_frequency(ending, rendered_sr), 660.0, delta=20.0)


if __name__ == "__main__":
    unittest.main()
