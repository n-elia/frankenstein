from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from film_tracks_aligner.models import Track, WarpMap, WarpSegment
from film_tracks_aligner.subtitle.adjuster import (
    _constant_subtitle_delay_ms,
    adjust_subtitle,
)


class SubtitleAdjusterTests(unittest.TestCase):
    def test_srt_timestamps_follow_linear_warp(self) -> None:
        track = Track(
            file_path=Path("audio.mkv"),
            stream_index=2,
            track_type="subtitle",
            codec="subrip",
            sub_format="srt",
        )
        warp_map = WarpMap(
            segments=[WarpSegment(0.0, 20.0, 0.0, 10.0)],
            ref_duration=20.0,
            sel_duration=10.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            subtitle_path = tmp / "input.srt"
            subtitle_path.write_text(
                "1\n00:00:02,000 --> 00:00:04,000\nHello\n",
                encoding="utf-8",
            )

            adjusted_path, warnings, delay_ms = adjust_subtitle(
                track, subtitle_path, warp_map, tmp
            )

            self.assertEqual(warnings, [])
            self.assertEqual(delay_ms, 0)
            self.assertEqual(
                adjusted_path.read_text(encoding="utf-8"),
                "1\n00:00:04,000 --> 00:00:08,000\nHello\n",
            )

    def test_bitmap_subtitle_returns_mux_delay(self) -> None:
        track = Track(
            file_path=Path("audio.mkv"),
            stream_index=3,
            track_type="subtitle",
            codec="hdmv_pgs_subtitle",
            sub_format="pgs",
        )
        warp_map = WarpMap(
            segments=[WarpSegment(0.0, 20.0, 5.0, 25.0)],
            ref_duration=20.0,
            sel_duration=25.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            subtitle_path = tmp / "input.sup"
            subtitle_path.write_bytes(b"dummy")

            adjusted_path, warnings, delay_ms = adjust_subtitle(
                track, subtitle_path, warp_map, tmp
            )

            self.assertEqual(adjusted_path, subtitle_path)
            self.assertEqual(delay_ms, -5000)
            self.assertIn("constant time offset", warnings[0])
            self.assertEqual(_constant_subtitle_delay_ms(warp_map), -5000)


if __name__ == "__main__":
    unittest.main()
