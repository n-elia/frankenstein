from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Log, ProgressBar, Static


class ProcessingScreen(Screen[bool]):
    """Progress screen shown during audio analysis and muxing.

    Runs the processing pipeline in a background thread and streams log messages.
    Dismisses with True on success, False on failure.
    """

    BINDINGS = [
        Binding("q", "quit_app", "Abort", show=True),
    ]

    CSS = """
    ProcessingScreen {
        align: center middle;
    }

    #panel {
        width: 90;
        height: auto;
        max-height: 90%;
        border: round $accent;
        padding: 1 2;
    }

    #title {
        text-align: center;
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #step-label {
        margin-bottom: 0;
        color: $text;
    }

    ProgressBar {
        margin-bottom: 1;
    }

    Log {
        height: 20;
        border: solid $panel;
        background: $surface;
    }
    """

    def __init__(
        self,
        pipeline_fn: Callable[[Callable[[str, float], None]], None],
        **kwargs,
    ) -> None:
        """
        Args:
            pipeline_fn: Callable that runs the pipeline. Receives a progress callback
                         progress_cb(message: str, fraction: float) and should call it
                         to report progress. Must be thread-safe.
        """
        super().__init__(**kwargs)
        self._pipeline_fn = pipeline_fn
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Static(id="panel"):
            yield Static("Processing…", id="title")
            yield Static("Starting…", id="step-label")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield Log(id="log", auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        thread.start()

    def _run_pipeline(self) -> None:
        try:
            self._pipeline_fn(self._on_progress)
            self.call_from_thread(self._finish, success=True)
        except Exception as exc:
            self._error = str(exc)
            self.call_from_thread(self._finish, success=False)

    def _on_progress(self, message: str, fraction: float) -> None:
        self.call_from_thread(self._update_ui, message, fraction)

    def _update_ui(self, message: str, fraction: float) -> None:
        self.query_one("#step-label", Static).update(message)
        progress = self.query_one("#progress", ProgressBar)
        progress.update(progress=int(fraction * 100))
        log = self.query_one("#log", Log)
        log.write_line(message)

    def _finish(self, success: bool) -> None:
        label = self.query_one("#step-label", Static)
        progress = self.query_one("#progress", ProgressBar)
        if success:
            label.update("Done!")
            progress.update(progress=100)
            log = self.query_one("#log", Log)
            log.write_line("--- Complete ---")
        else:
            label.update(f"Error: {self._error}")
            log = self.query_one("#log", Log)
            log.write_line(f"FAILED: {self._error}")
        self.dismiss(success)

    def action_quit_app(self) -> None:
        self.dismiss(False)
