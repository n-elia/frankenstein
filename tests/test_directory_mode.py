from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from film_tracks_aligner import cli
from film_tracks_aligner.models import Track, TrackSelection


class DirectoryModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_resolve_input_files_discovers_sorted_mkvs(self) -> None:
        with self.runner.isolated_filesystem():
            Path("b.mkv").touch()
            Path("a.mkv").touch()
            Path("note.txt").touch()

            files, discovered = cli._resolve_input_files(None, None)

        self.assertTrue(discovered)
        self.assertEqual([path.name for path in files], ["a.mkv", "b.mkv"])

    def test_cli_without_args_discovers_directory_and_defaults_output(self) -> None:
        captured: dict[str, object] = {}

        with self.runner.isolated_filesystem():
            file_a = Path("b_source.mkv")
            file_b = Path("a_source.mkv")
            file_a.touch()
            file_b.touch()
            expected_output = Path.cwd() / "a_source_merged.mkv"

            selection = _selection_for(video_path=file_b, audio_path=file_a)

            class FakeApp:
                def __init__(self, files: list[Path], output_path: Path | None) -> None:
                    captured["files"] = files
                    captured["app_output"] = output_path

                def run(self) -> TrackSelection:
                    return selection

            def fake_run_pipeline(
                selected: TrackSelection,
                tmp: Path,
                output: Path,
            ) -> None:
                captured["pipeline_selection"] = selected
                captured["pipeline_output"] = output

            with patch("film_tracks_aligner.cli.FilmAlignerApp", FakeApp), patch(
                "film_tracks_aligner.cli._run_pipeline", fake_run_pipeline
            ):
                result = self.runner.invoke(cli.app, [])

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertEqual(
            [path.name for path in captured["files"]],  # type: ignore[index]
            ["a_source.mkv", "b_source.mkv"],
        )
        self.assertIsNone(captured["app_output"])
        self.assertEqual(
            captured["pipeline_output"],  # type: ignore[index]
            expected_output,
        )

    def test_cli_with_explicit_files_preserves_two_file_behavior(self) -> None:
        captured: dict[str, object] = {}

        with self.runner.isolated_filesystem():
            file1 = Path("video.mkv")
            file2 = Path("audio.mkv")
            file1.touch()
            file2.touch()
            expected_output = Path.cwd() / "video_merged.mkv"

            selection = _selection_for(video_path=file1, audio_path=file2)

            class FakeApp:
                def __init__(self, files: list[Path], output_path: Path | None) -> None:
                    captured["files"] = files
                    captured["app_output"] = output_path

                def run(self) -> TrackSelection:
                    return selection

            def fake_run_pipeline(
                selected: TrackSelection,
                tmp: Path,
                output: Path,
            ) -> None:
                captured["pipeline_output"] = output

            with patch("film_tracks_aligner.cli.FilmAlignerApp", FakeApp), patch(
                "film_tracks_aligner.cli._run_pipeline", fake_run_pipeline
            ):
                result = self.runner.invoke(cli.app, [str(file1), str(file2)])

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertEqual(captured["files"], [Path("video.mkv"), Path("audio.mkv")])
        self.assertEqual(
            captured["pipeline_output"],  # type: ignore[index]
            expected_output,
        )

    def test_cli_rejects_single_positional_argument(self) -> None:
        with self.runner.isolated_filesystem():
            Path("only.mkv").touch()
            result = self.runner.invoke(cli.app, ["only.mkv"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("provide either two MKV files or no positional arguments", result.stdout)


def _selection_for(video_path: Path, audio_path: Path) -> TrackSelection:
    video = Track(
        file_path=video_path,
        stream_index=0,
        track_type="video",
        codec="hevc",
    )
    audio = Track(
        file_path=audio_path,
        stream_index=1,
        track_type="audio",
        codec="ac3",
        sample_rate=48000,
        channels=2,
    )
    return TrackSelection(
        video=video,
        audio=audio,
        subtitle=None,
        reference_audio=audio,
    )


if __name__ == "__main__":
    unittest.main()
