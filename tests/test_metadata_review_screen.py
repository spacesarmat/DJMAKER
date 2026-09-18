"""Регрессия UI 'Метаданные': кнопки массового поиска и список 'Требуют внимания'."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.settings import AppSettings
from djmaker.ui.navigation_cards_controls import NavigationCardsController


def _track(
    track_id: int,
    *,
    title: str = "Song",
    artist: str = "Artist",
    needs_review: bool = False,
    reason: str = "",
    score: float | None = None,
) -> TrackRecord:
    return TrackRecord(
        id=track_id,
        path=Path(f"/music/{title}.mp3"),
        root_path=Path("/music"),
        size=1,
        mtime_ns=0,
        extension=".mp3",
        file_hash=str(track_id),
        metadata=AudioMetadata(title=title, artist=artist),
        technical=AudioTechnicalInfo(),
        needs_metadata_review=needs_review,
        metadata_review_reason=reason,
        metadata_review_score=score,
    )


class MetadataReviewScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.service = Mock()
        self.app.service.database = Mock()
        self.app._notify = Mock()
        self.app._start_metadata_bulk_search = Mock()
        self.app._dismiss_metadata_review = Mock()
        self.app._metadata_search = Mock()
        self.app.page = Mock()
        self.app.navigation = SimpleNamespace(selected_index=3)
        self.app._selected_library_track_ids = set()
        self.controller = NavigationCardsController(self.app)

    def _find_buttons(self, control: ft.Control) -> list[ft.Button]:
        found: list[ft.Button] = []
        stack = [control]
        while stack:
            current = stack.pop()
            if isinstance(current, ft.Button):
                found.append(current)
            content = getattr(current, "content", None)
            if isinstance(content, ft.Control):
                stack.append(content)
            for child in getattr(current, "controls", None) or []:
                stack.append(child)
        return found

    def test_selected_button_shows_count_and_is_disabled_when_empty(self) -> None:
        card = self.controller.bulk_metadata_search_card()
        buttons = self._find_buttons(card)
        selected_button = next(b for b in buttons if "выделенных" in str(b.content))
        self.assertIn("(0)", selected_button.content)
        self.assertTrue(selected_button.disabled)

    def test_selected_button_enabled_and_counts_when_tracks_selected(self) -> None:
        self.app._selected_library_track_ids = {1, 2, 3}
        card = self.controller.bulk_metadata_search_card()
        buttons = self._find_buttons(card)
        selected_button = next(b for b in buttons if "выделенных" in str(b.content))
        self.assertIn("(3)", selected_button.content)
        self.assertFalse(selected_button.disabled)

    def test_start_all_fetches_full_library_and_starts_bulk_search(self) -> None:
        tracks = [_track(1), _track(2)]
        self.app.service.tracks = Mock(return_value=tracks)
        card = self.controller.bulk_metadata_search_card()
        buttons = self._find_buttons(card)
        all_button = next(b for b in buttons if "всех" in str(b.content))

        all_button.on_click(None)

        self.app.service.tracks.assert_called_once_with(limit=10_000)
        self.app._start_metadata_bulk_search.assert_called_once_with(tracks)

    def test_start_selected_resolves_ids_to_tracks(self) -> None:
        track = _track(5)
        self.app._selected_library_track_ids = {5}
        self.app.service.database.get_track = Mock(return_value=track)
        card = self.controller.bulk_metadata_search_card()
        buttons = self._find_buttons(card)
        selected_button = next(b for b in buttons if "выделенных" in str(b.content))

        selected_button.on_click(None)

        self.app.service.database.get_track.assert_called_once_with(5)
        self.app._start_metadata_bulk_search.assert_called_once_with([track])

    def test_review_card_lists_flagged_tracks_with_reason_and_score(self) -> None:
        flagged = [
            _track(1, needs_review=True, reason="low_confidence", score=0.62),
            _track(2, needs_review=True, reason="not_found", score=None),
        ]
        self.app.service.database.list_tracks_needing_review = Mock(return_value=flagged)

        card = self.controller.metadata_review_card()

        rendered = repr(card)
        self.assertIn("Низкое совпадение", rendered)
        self.assertIn("62%", rendered)
        self.assertIn("Не найдено", rendered)

    def test_review_card_shows_empty_state_when_nothing_flagged(self) -> None:
        self.app.service.database.list_tracks_needing_review = Mock(return_value=[])

        card = self.controller.metadata_review_card()

        self.assertIn("нет", repr(card))

    def test_review_card_find_button_opens_existing_search_dialog(self) -> None:
        flagged = [_track(7, needs_review=True, reason="not_found")]
        self.app.service.database.list_tracks_needing_review = Mock(return_value=flagged)

        card = self.controller.metadata_review_card()
        buttons = self._find_buttons(card)
        find_button = next(b for b in buttons if str(b.content) == "Найти")
        find_button.on_click(None)

        self.app.page.run_task.assert_called_once_with(self.app._metadata_search, 7)

    def test_dismiss_metadata_review_clears_flag(self) -> None:
        self.app.navigation.selected_index = 0

        self.controller.dismiss_metadata_review(7)

        self.app.service.database.clear_metadata_review.assert_called_once_with(7)

    def test_dismiss_refreshes_screen_when_on_metadata_tab(self) -> None:
        self.app.navigation.selected_index = 3
        self.controller.show_plugins = Mock()

        self.controller.dismiss_metadata_review(7)

        self.controller.show_plugins.assert_called_once()

    def test_dismiss_does_not_refresh_when_on_other_tab(self) -> None:
        self.app.navigation.selected_index = 0
        self.controller.show_plugins = Mock()

        self.controller.dismiss_metadata_review(7)

        self.controller.show_plugins.assert_not_called()


class ProviderStatusRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = SimpleNamespace()
        self.app.settings = AppSettings(metadata_providers=("musicbrainz",))
        self.app.theme_settings = Mock()
        self.app.service = Mock()
        self.controller = NavigationCardsController(self.app)

    @staticmethod
    def _provider(provider_id: str, display_name: str, configured: bool | None = None) -> Mock:
        provider = Mock()
        provider.provider_id = provider_id
        provider.display_name = display_name
        if configured is None:
            del provider.is_configured
        else:
            provider.is_configured = Mock(return_value=configured)
        return provider

    def _chips(self, providers: list[Mock]) -> list[ft.Container]:
        self.app.service.plugins.all = Mock(return_value=providers)
        card = self.controller.provider_status_row()
        return list(card.content.controls)

    def test_selected_provider_is_highlighted(self) -> None:
        chips = self._chips([self._provider("musicbrainz", "MusicBrainz")])

        self.assertEqual(chips[0].bgcolor, ft.Colors.PRIMARY)

    def test_unselected_provider_is_not_highlighted(self) -> None:
        chips = self._chips([self._provider("deezer", "Deezer")])

        self.assertEqual(chips[0].bgcolor, ft.Colors.SURFACE_CONTAINER_HIGH)

    def test_unconfigured_provider_is_not_highlighted_even_if_selected(self) -> None:
        self.app.settings = AppSettings(metadata_providers=("spotify",))
        chips = self._chips([self._provider("spotify", "Spotify", configured=False)])

        self.assertEqual(chips[0].bgcolor, ft.Colors.SURFACE_CONTAINER_HIGH)
        self.assertIn("не настроен", chips[0].tooltip)

    def test_click_on_configured_provider_toggles_it(self) -> None:
        chips = self._chips([self._provider("deezer", "Deezer")])

        chips[0].on_click(None)

        self.app.theme_settings.toggle_metadata_provider.assert_called_once_with("deezer")
        self.app.theme_settings.open_provider_configuration_dialog.assert_not_called()

    def test_click_on_unconfigured_provider_opens_configuration_dialog(self) -> None:
        chips = self._chips([self._provider("spotify", "Spotify", configured=False)])

        chips[0].on_click(None)

        self.app.theme_settings.open_provider_configuration_dialog.assert_called_once_with(
            "spotify"
        )
        self.app.theme_settings.toggle_metadata_provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
