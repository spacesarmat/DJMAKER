from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from djmaker.domain.beat_grid import BeatGridAnchor
from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    BeatGridAnalysis,
    TrackRecord,
    WaveformAnalysis,
)
from djmaker.infrastructure.beat_grid import BeatGridRepository
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.ui.app import DJMakerUI
from djmaker.ui.beat_grid_editor import (
    BeatGridEditorState,
    beat_grid_editor_svg,
)


def grid() -> BeatGridAnalysis:
    return BeatGridAnalysis(
        bpm=120.0,
        first_beat_ms=500,
        downbeat_ms=1000,
        beat_ticks_ms=tuple(range(500, 10_001, 500)),
        tempo_stability=0.96,
        downbeat_confidence=0.72,
    )


def track() -> TrackRecord:
    return TrackRecord(
        id=1,
        path=Path("track.flac"),
        root_path=Path("."),
        size=1,
        mtime_ns=1,
        extension=".flac",
        file_hash="hash",
        metadata=AudioMetadata(title="Track", artist="Artist"),
        technical=AudioTechnicalInfo(duration=12.0),
        analysis=AudioAnalysis(bpm=120.0, beat_grid=grid()),
        waveform=WaveformAnalysis(
            peaks=tuple((index % 10) / 10 for index in range(2048))
        ),
    )


class BeatGridEditorStateTests(unittest.TestCase):
    def test_zoom_cursor_and_warp_marker_update_preview(self) -> None:
        state = BeatGridEditorState.from_track(track(), [])
        original_view = state.view_duration_ms
        state.set_cursor_fraction(0.75)
        cursor = state.cursor_ms
        state.zoom(0.5)

        self.assertLess(state.view_duration_ms, original_view)
        self.assertLessEqual(state.view_start_ms, cursor)
        self.assertGreaterEqual(state.view_end_ms, cursor)

        anchor = state.add_warp_at_cursor()
        self.assertEqual(cursor, anchor.source_ms)
        self.assertEqual(1, len(state.anchors))
        state.square_bars = 4
        svg = beat_grid_editor_svg(state)
        self.assertIn("#FF4D8D", svg)
        self.assertIn("#00F0FF", svg)
        self.assertIn("#FFB300", svg)

    def test_manual_downbeat_can_leave_original_detected_ticks(self) -> None:
        state = BeatGridEditorState.from_track(track(), [])
        state.apply_grid(
            bpm=121.25,
            first_beat_ms=333,
            downbeat_ms=777,
            beats_per_bar=4,
        )
        self.assertEqual("manual", state.grid.source)
        self.assertEqual(777, state.grid.downbeat_ms)


class BeatGridEditorDialogTests(unittest.TestCase):
    def test_library_row_editor_opens_for_analyzed_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = LibraryDatabase(Path(temp) / "library.sqlite3")
            database.initialize()
            root = Path(temp)
            source = root / "track.flac"
            source.write_bytes(b"audio")
            stat = source.stat()
            database.upsert_track(
                path=source,
                root=root,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                extension=source.suffix,
                file_hash="hash",
                metadata=AudioMetadata(title="Track", artist="Artist"),
                technical=AudioTechnicalInfo(duration=12.0),
                scan_token="scan",
            )
            stored = database.list_tracks()[0]
            database.save_audio_analysis(
                stored.id,
                AudioAnalysis(bpm=120.0, beat_grid=grid()),
            )
            database.save_waveform_analysis(
                stored.id,
                WaveformAnalysis(peaks=(0.2, 0.8) * 1024),
            )
            ui = DJMakerUI.__new__(DJMakerUI)
            ui.service = SimpleNamespace(
                track=lambda track_id: database.get_track(track_id),
                beat_grid=BeatGridRepository(database),
            )
            ui.page = Mock()
            ui._notify = Mock()
            ui.show_library = Mock()
            ui._selected_track_id = None

            ui._open_beat_grid_editor(stored.id)

            ui.page.show_dialog.assert_called_once()
            dialog = ui.page.show_dialog.call_args.args[0]
            self.assertIn("Редактор BPM-сетки", dialog.title.value)
            self.assertEqual(
                "Сохранить сетку",
                dialog.actions[-1].content,
            )


if __name__ == "__main__":
    unittest.main()
