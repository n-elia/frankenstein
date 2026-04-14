from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from film_tracks_aligner.mkv.inspector import (
    get_reference_audio,
    get_tracks_by_type,
    inspect_file,
)
from film_tracks_aligner.models import Track, TrackSelection
from film_tracks_aligner.tui.screens.theme_select import ThemeSelectScreen
from film_tracks_aligner.tui.screens.track_select import TrackSelectScreen


class FilmAlignerApp(App[TrackSelection | None]):
    """Main Textual application.

    Walks the user through 3 selection screens (video → audio → subtitle)
    and returns a TrackSelection, or None if the user cancelled.
    """

    TITLE = "Frankenstein"
    SUB_TITLE = "MKV track selector & audio aligner"

    BINDINGS = [
        Binding("t", "select_theme", "Theme", show=True),
    ]

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(self, files: list[Path], output_path: Path | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._files = files
        self._output_path = output_path
        self._all_tracks: list[Track] = []

    def action_select_theme(self) -> None:
        self.push_screen(ThemeSelectScreen(current_theme=self.theme))

    def on_mount(self) -> None:
        # Load tracks synchronously before starting the selection flow.
        # This is acceptable here because inspection is fast (ffprobe on metadata only).
        self._all_tracks = []
        for file_path in self._files:
            self._all_tracks.extend(inspect_file(file_path))
        self._start_video_selection()

    # ---- Selection flow ----

    def _start_video_selection(self) -> None:
        video_tracks = get_tracks_by_type(self._all_tracks, "video")
        screen = TrackSelectScreen(
            step_label="Step 1/3 — Select Video Track",
            tracks=video_tracks,
            allow_none=False,
        )
        self.push_screen(screen, self._on_video_selected)

    def _on_video_selected(self, video: Track | None) -> None:
        if video is None:
            self.exit(None)
            return
        self._selected_video = video
        audio_tracks = get_tracks_by_type(self._all_tracks, "audio")
        screen = TrackSelectScreen(
            step_label="Step 2/3 — Select Audio Track",
            tracks=audio_tracks,
            allow_none=False,
        )
        self.push_screen(screen, self._on_audio_selected)

    def _on_audio_selected(self, audio: Track | None) -> None:
        if audio is None:
            self.exit(None)
            return
        self._selected_audio = audio
        subtitle_tracks = get_tracks_by_type(self._all_tracks, "subtitle")
        screen = TrackSelectScreen(
            step_label="Step 3/3 — Select Subtitle Track (optional)",
            tracks=subtitle_tracks,
            allow_none=True,
        )
        self.push_screen(screen, self._on_subtitle_selected)

    def _on_subtitle_selected(self, subtitle: Track | None) -> None:
        # subtitle is None when the user chose "None / Skip" or pressed ESC
        # We need to distinguish ESC (go back) from explicit "None" choice.
        # TrackSelectScreen.action_cancel() dismisses with None too,
        # so we use a sentinel approach: subtitle tracks list is empty → same.
        # Simplification: treat None as "no subtitle" and proceed.
        self._selected_subtitle = subtitle

        reference_audio = get_reference_audio(
            self._selected_video.file_path, self._all_tracks
        )
        if reference_audio is None:
            # No audio in the video file: cannot compute sync — use the selected audio as-is
            reference_audio = self._selected_audio

        selection = TrackSelection(
            video=self._selected_video,
            audio=self._selected_audio,
            subtitle=self._selected_subtitle,
            reference_audio=reference_audio,
            output_path=self._output_path,
        )
        self.exit(selection)
