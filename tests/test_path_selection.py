from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from frankenstein.cli import _run_pipeline
from frankenstein.models import Track, TrackSelection, WarpMap, WarpSegment


class _FakeAlignmentDisplay:
    def __init__(self, *_: object, **__: object) -> None:
        pass

    def __enter__(self) -> "_FakeAlignmentDisplay":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def on_progress(self, _: str) -> None:
        return None

    def on_chunk(self, *_: object) -> None:
        return None


class PathSelectionTests(unittest.TestCase):
    def test_linear_full_overlap_routes_to_lossless_mux(self) -> None:
        warp_map = WarpMap(
            segments=[WarpSegment(0.0, 10.0, 0.0, 10.0)],
            ref_duration=10.0,
            sel_duration=10.0,
        )
        selection = _selection()

        with tempfile.TemporaryDirectory(prefix="frankensync-path-") as tmpdir:
            tmp = Path(tmpdir)
            output = tmp / "output.mkv"

            with patch("frankenstein.cli.extract_audio_as_wav"), patch(
                "frankenstein.cli.extract_features",
                side_effect=[
                    (np.zeros((12, 8)), np.zeros(8), 10.0),
                    (np.zeros((12, 8)), np.zeros(8), 10.0),
                ],
            ), patch(
                "frankenstein.cli.compute_warp_map", return_value=(warp_map, 0.9)
            ), patch(
                "frankenstein.cli.apply_warp", return_value=None
            ) as apply_warp_mock, patch(
                "frankenstein.cli.mux_output"
            ) as mux_output_mock, patch(
                "frankenstein.cli.AlignmentDisplay", _FakeAlignmentDisplay
            ):
                _run_pipeline(selection, tmp, output)

        apply_warp_mock.assert_called_once()
        self.assertIsNone(mux_output_mock.call_args.kwargs["warped_audio"])
        self.assertEqual(mux_output_mock.call_args.kwargs["warp_map"], warp_map)

    def test_linear_unmatched_edges_routes_to_rendered_trim(self) -> None:
        warp_map = WarpMap(
            segments=[WarpSegment(0.0, 10.0, 1.0, 11.0)],
            ref_duration=10.0,
            sel_duration=12.0,
        )
        selection = _selection()
        rendered = (Path("rendered.aac"), 0)

        with tempfile.TemporaryDirectory(prefix="frankensync-path-") as tmpdir:
            tmp = Path(tmpdir)
            output = tmp / "output.mkv"

            with patch("frankenstein.cli.extract_audio_as_wav"), patch(
                "frankenstein.cli.extract_features",
                side_effect=[
                    (np.zeros((12, 8)), np.zeros(8), 10.0),
                    (np.zeros((12, 8)), np.zeros(8), 12.0),
                ],
            ), patch(
                "frankenstein.cli.compute_warp_map", return_value=(warp_map, 0.9)
            ), patch(
                "frankenstein.cli.apply_warp", return_value=rendered
            ) as apply_warp_mock, patch(
                "frankenstein.cli.mux_output"
            ) as mux_output_mock, patch(
                "frankenstein.cli.AlignmentDisplay", _FakeAlignmentDisplay
            ):
                _run_pipeline(selection, tmp, output)

        apply_warp_mock.assert_called_once()
        self.assertEqual(mux_output_mock.call_args.kwargs["warped_audio"], rendered)
        self.assertEqual(mux_output_mock.call_args.kwargs["warp_map"], warp_map)


def _selection() -> TrackSelection:
    video = Track(
        file_path=Path("video.mkv"),
        stream_index=0,
        track_type="video",
        codec="hevc",
    )
    selected_audio = Track(
        file_path=Path("selected_audio.mkv"),
        stream_index=1,
        track_type="audio",
        codec="ac3",
        channels=2,
        sample_rate=48000,
    )
    reference_audio = Track(
        file_path=Path("video.mkv"),
        stream_index=2,
        track_type="audio",
        codec="ac3",
        channels=2,
        sample_rate=48000,
    )
    return TrackSelection(
        video=video,
        audio=selected_audio,
        subtitle=None,
        reference_audio=reference_audio,
    )


if __name__ == "__main__":
    unittest.main()
