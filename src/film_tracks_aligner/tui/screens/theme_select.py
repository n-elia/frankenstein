from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static


class ThemeSelectScreen(Screen[str | None]):
    """Live theme picker: the app theme updates immediately as the cursor moves.

    Dismisses with the selected theme name, or None if the user cancelled
    (in which case the caller is responsible for restoring the original theme).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    ThemeSelectScreen {
        align: center middle;
    }

    #panel {
        width: 50;
        height: auto;
        max-height: 80%;
        border: round $accent;
        padding: 1 2;
    }

    #title {
        text-align: center;
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    ListView {
        height: auto;
        max-height: 25;
        border: solid $panel;
    }

    ListItem {
        padding: 0 1;
    }

    #hint {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, current_theme: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._original_theme = current_theme
        self._current_theme = current_theme

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Static(id="panel"):
            yield Static("Select Theme", id="title")
            yield ListView(*self._build_items(), id="theme-list")
            yield Static("↑↓ preview live   ENTER confirm   ESC cancel", id="hint")
        yield Footer()

    def _build_items(self) -> list[ListItem]:
        items: list[ListItem] = []
        for name in self.app.available_themes:
            item = ListItem(Label(name))
            item._theme_name = name  # type: ignore[attr-defined]
            items.append(item)
        return items

    def on_mount(self) -> None:
        lv = self.query_one("#theme-list", ListView)
        theme_names = list(self.app.available_themes.keys())
        if self._original_theme in theme_names:
            lv.index = theme_names.index(self._original_theme)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        name: str = getattr(event.item, "_theme_name", "")
        if name:
            self._current_theme = name
            self.app.theme = name  # type: ignore[assignment]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name: str = getattr(event.item, "_theme_name", "")
        if name:
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.app.theme = self._original_theme  # type: ignore[assignment]
        self.dismiss(None)
