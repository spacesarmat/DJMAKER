from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.domain.beat_grid import (
    BeatGridError,
    nearest_grid_position,
    regular_grid_markers,
    validate_beat_grid,
)
from djmaker.domain.models import (
    AudioMetadata,
    AudioTechnicalInfo,
    BeatGridAnalysis,
)
from djmaker.infrastructure.beat_grid import BeatGridAnchor, BeatGridRepository
from djmaker.infrastructure.database import LibraryDatabase


class BeatGridDomainTests(unittest.TestCase):
    @staticmethod
    def _grid() -> BeatGridAnalysis:
        return BeatGridAnalysis(
            bpm=120.0,
            first_beat_ms=500,
            downbeat_ms=1000,
            beat_ticks_ms=(500, 1000, 1500, 2000),
            tempo_stability=0.96,
            downbeat_confidence=0.7,
        )

    def test_regular_grid_marks_beats_bars_and_square_origin(self) -> None:
        markers = regular_grid_markers(self._grid(), 3000, square_bars=8)

        self.assertEqual([0, 500, 1000, 1500, 2000, 2500, 3000], [
            marker.position_ms for marker in markers
        ])
        origin = next(marker for marker in markers if marker.position_ms == 1000)
        self.assertEqual(0, origin.beat_number)
        self.assertTrue(origin.is_bar)
        self.assertTrue(origin.is_square)

    def test_nearest_grid_position_supports_beat_and_bar_steps(self) -> None:
        grid = self._grid()
        self.assertEqual(1500, nearest_grid_position(grid, 1430))
        self.assertEqual(1000, nearest_grid_position(grid, 1430, beats=4))

    def test_invalid_grid_is_rejected(self) -> None:
        with self.assertRaises(BeatGridError):
            validate_beat_grid(
                BeatGridAnalysis(
                    bpm=500.0,
                    first_beat_ms=0,
                    downbeat_ms=0,
                )
            )

        with self.assertRaisesRegex(BeatGridError, "не содержит"):
            validate_beat_grid(
                BeatGridAnalysis(
                    bpm=120.0,
                    first_beat_ms=0,
                    downbeat_ms=0,
                )
            )


class BeatGridRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "music"
        self.root.mkdir()
        self.path = self.root / "track.flac"
        self.path.write_bytes(b"audio")
        self.database = LibraryDatabase(Path(self.temp.name) / "library.sqlite3")
        self.database.initialize()
        stat = self.path.stat()
        self.database.upsert_track(
            path=self.path,
            root=self.root,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extension=self.path.suffix,
            file_hash="hash",
            metadata=AudioMetadata(title="Track"),
            technical=AudioTechnicalInfo(duration=60.0),
            scan_token="scan",
        )
        self.track_id = self.database.list_tracks()[0].id
        self.repository = BeatGridRepository(self.database)

    def test_anchors_are_saved_updated_ordered_and_cleared(self) -> None:
        self.repository.save_anchor(BeatGridAnchor(self.track_id, 2000, 4.0))
        self.repository.save_anchor(BeatGridAnchor(self.track_id, 1000, 0.0))
        self.repository.save_anchor(BeatGridAnchor(self.track_id, 2000, 4.25))

        anchors = self.repository.anchors(self.track_id)
        self.assertEqual([1000, 2000], [anchor.source_ms for anchor in anchors])
        self.assertEqual(4.25, anchors[1].beat_number)

        self.repository.clear_anchors(self.track_id)
        self.assertEqual([], self.repository.anchors(self.track_id))

    def test_track_delete_cascades_to_anchors(self) -> None:
        self.repository.save_anchor(BeatGridAnchor(self.track_id, 1000, 0.0))
        with self.database.connection() as conn:
            conn.execute("DELETE FROM tracks WHERE id=?", (self.track_id,))
            conn.commit()

        self.assertEqual([], self.repository.anchors(self.track_id))


if __name__ == "__main__":
    unittest.main()
