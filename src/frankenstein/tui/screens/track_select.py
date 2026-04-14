from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from frankenstein.models import Track


class TrackItem(ListItem):
    """A selectable list item representing one Track."""

    def __init__(self, track: Track, **kwargs) -> None:
        super().__init__(**kwargs)
        self.track = track

    def compose(self) -> ComposeResult:
        if self.track.track_type == "audio":
            yield from self._compose_audio()
        elif self.track.track_type == "subtitle":
            yield from self._compose_subtitle()
        else:
            yield Label(self.track.display_label)

    def _compose_subtitle(self) -> ComposeResult:
        t = self.track

        # --- Row 1: language + title + badges ---
        lang = t.language.upper() if t.language else "?"
        title_part = f'  "{t.title}"' if t.title else ""
        badges = "".join(
            f"  [{b}]" for b in t.sub_badges
        )
        yield Label(f"[bold]{lang}[/bold]{title_part}{badges}", classes="sub-identity")

        # --- Row 2: technical details ---
        details = [f"#{t.stream_index}", t.sub_format_display]
        yield Label("  ·  ".join(details), classes="sub-details")

    def _compose_audio(self) -> ComposeResult:
        t = self.track

        # --- Row 1: identity (language + title) ---
        lang = t.language.upper() if t.language else "UND"
        title_part = f'  "{t.title}"' if t.title else ""
        yield Label(f"[bold]{lang}[/bold]{title_part}", classes="audio-identity")

        # --- Row 2: technical details ---
        details: list[str] = [
            f"#{t.stream_index}",
            t.audio_codec_display,
        ]
        if layout := t.audio_layout_display:
            details.append(layout)
        if t.sample_rate:
            details.append(f"{t.sample_rate // 1000} kHz")
        if br := t.audio_bitrate_display:
            details.append(br)
        yield Label("  ·  ".join(details), classes="audio-details")


class TrackSelectScreen(Screen[Track | None]):
    """Full-screen track selector.

    Params:
        step_label: e.g. "Step 1/3 — Video Track"
        tracks: list of Track objects to display, grouped by file
        allow_none: if True, a "None / Skip" option is shown at the top
    """

    BINDINGS = [
        Binding("escape", "cancel", "Back / Cancel", show=True),
    ]

    CSS = """
    TrackSelectScreen {
        align: center middle;
    }

    #panel {
        width: 90;
        height: auto;
        max-height: 90%;
        border: round $accent;
        padding: 1 2;
    }

    #step-label {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
        text-style: bold;
    }

    #file-separator {
        color: $text-muted;
        margin-top: 1;
    }

    ListView {
        height: auto;
        max-height: 30;
        border: solid $panel;
    }

    ListItem {
        padding: 0 1;
        height: auto;
    }

    ListItem:hover {
        background: $boost;
    }

    ListItem.-highlighted {
        background: $accent 30%;
    }

    .audio-identity {
        text-style: bold;
    }

    .audio-details {
        color: $text-muted;
        text-style: italic;
    }

    .sub-identity {
        text-style: bold;
    }

    .sub-details {
        color: $text-muted;
        text-style: italic;
    }

    #hint {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        step_label: str,
        tracks: list[Track],
        allow_none: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._step_label = step_label
        self._tracks = tracks
        self._allow_none = allow_none

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Static(id="panel"):
            yield Static(self._step_label, id="step-label")
            yield ListView(*self._build_items(), id="track-list")
            yield Static("↑↓ navigate   ENTER confirm   ESC back", id="hint")
        yield Footer()

    def _build_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        if self._allow_none:
            none_item = ListItem(Label("  [None / Skip]"))
            none_item._is_none = True  # type: ignore[attr-defined]
            items.append(none_item)

        current_file: Path | None = None
        for track in self._tracks:
            if track.file_path != current_file:
                current_file = track.file_path
                sep = ListItem(Label(f"  {current_file.name}", id=f"sep-{id(current_file)}"))
                sep._is_separator = True  # type: ignore[attr-defined]
                sep.disabled = True
                items.append(sep)
            items.append(TrackItem(track))
        return items

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if getattr(item, "_is_separator", False):
            return
        if getattr(item, "_is_none", False):
            self.dismiss(None)
            return
        if isinstance(item, TrackItem):
            self.dismiss(item.track)

    def action_cancel(self) -> None:
        self.dismiss(None)
