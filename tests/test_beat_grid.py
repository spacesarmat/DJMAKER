from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.domain.beat_grid import (
    BeatGridAnchor,
    BeatGridError,
    beat_number_at_position,
    beat_position_ms,
    nearest_grid_position,
    nearest_warped_grid_position,
    regular_grid_markers,
    validate_anchor_mapping,
    validate_beat_grid,
    warped_grid_markers,
)
from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    BeatGridAnalysis,
)
from djmaker.infrastructure.beat_grid import BeatGridRepository
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

    def test_warp_anchors_interpolate_and_inverse_positions(self) -> None:
        grid = self._grid()
        anchors = (
            BeatGridAnchor(track_id=1, source_ms=3200, beat_number=4.0),
            BeatGridAnchor(track_id=1, source_ms=5000, beat_number=8.0),
        )

        self.assertEqual(2100, beat_position_ms(grid, 2, anchors))
        self.assertEqual(4100, beat_position_ms(grid, 6, anchors))
        self.assertAlmostEqual(6.0, beat_number_at_position(grid, 4100, anchors))
        self.assertEqual((4100, 6), nearest_warped_grid_position(grid, 4070, anchors))
        markers = warped_grid_markers(grid, 6000, anchors=anchors)
        self.assertIn(3200, [marker.position_ms for marker in markers])
        self.assertIn(5000, [marker.position_ms for marker in markers])

    def test_warp_anchors_reject_crossing_and_extreme_segment_tempo(self) -> None:
        grid = self._grid()
        with self.assertRaisesRegex(BeatGridError, "вперёд"):
            validate_anchor_mapping(
                grid,
                [
                    BeatGridAnchor(1, 3000, 4.0),
                    BeatGridAnchor(1, 2500, 8.0),
                ],
            )
        with self.assertRaisesRegex(BeatGridError, "вне диапазона"):
            validate_anchor_mapping(
                grid,
                [
                    BeatGridAnchor(1, 3000, 4.0),
                    BeatGridAnchor(1, 3100, 8.0),
                ],
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

    def test_editor_state_is_saved_atomically_with_manual_grid(self) -> None:
        analyzed_track = self.database.get_track(self.track_id)
        assert analyzed_track is not None
        self.database.save_audio_analysis(
            self.track_id,
            AudioAnalysis(
                bpm=120.0,
                beat_grid=BeatGridAnalysis(
                    bpm=120.0,
                    first_beat_ms=250,
                    downbeat_ms=750,
                    beat_ticks_ms=(250, 750, 1250, 1750),
                ),
            ),
        )
        grid = BeatGridAnalysis(
            bpm=124.5,
            first_beat_ms=300,
            downbeat_ms=800,
            beat_ticks_ms=(250, 750, 1250, 1750),
            tempo_stability=0.9,
            downbeat_confidence=0.6,
            source="manual",
        )
        anchors = [BeatGridAnchor(self.track_id, 2800, 4.0)]

        self.repository.save_editor_state(self.track_id, grid, anchors)

        track = self.database.get_track(self.track_id)
        self.assertIsNotNone(track)
        assert track is not None and track.analysis is not None
        self.assertAlmostEqual(124.5, track.analysis.bpm or 0.0)
        self.assertIsNotNone(track.analysis.beat_grid)
        assert track.analysis.beat_grid is not None
        self.assertEqual("manual", track.analysis.beat_grid.source)
        self.assertEqual(800, track.analysis.beat_grid.downbeat_ms)
        self.assertEqual(anchors, self.repository.anchors(self.track_id))


if __name__ == "__main__":
    unittest.main()
