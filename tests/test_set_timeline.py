"""Музыкальная сетка, Cue-точки, SQLite и FFmpeg-preview перехода."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo
from djmaker.domain.set_timeline import (
    SetTimelineError,
    TimelineTransition,
    build_transition_plan,
    build_playlist_timeline,
    move_by_beats,
    snap_to_beat,
    snap_to_square,
)
from djmaker.infrastructure.database import LibraryDatabase
from djmaker.infrastructure.playlists import PlaylistRepository
from djmaker.infrastructure.set_timeline import (
    SavedTransition,
    SetTimelineRepository,
    SetTimelineRepositoryError,
)
from djmaker.services.transition_preview import (
    build_transition_preview_command,
    render_transition_preview,
)


class SetTimelineDomainTests(unittest.TestCase):
    def test_snap_and_nudge_follow_beat_grid_anchor(self) -> None:
        self.assertEqual(snap_to_beat(1_220, 120, anchor_ms=250), 1_250)
        self.assertEqual(move_by_beats(1_250, 4, 120), 3_250)
        self.assertEqual(move_by_beats(250, -4, 120), 0)
        self.assertEqual(snap_to_square(17_900, 120, 8, anchor_ms=250), 16_250)

    def test_plan_uses_square_and_syncs_incoming_bpm(self) -> None:
        plan = build_transition_plan(
            outgoing_cue_ms=10_000,
            incoming_cue_ms=2_000,
            outgoing_bpm=120,
            incoming_bpm=125,
            outgoing_duration_ms=40_000,
            incoming_duration_ms=40_000,
            bars_per_square=8,
            square_count=1,
        )
        self.assertEqual(plan.overlap_ms, 16_000)
        self.assertEqual(plan.preview_margin_ms, 4_000)
        self.assertAlmostEqual(plan.incoming_tempo, 0.96)

    def test_plan_rejects_unknown_bpm_and_short_tail(self) -> None:
        arguments = dict(
            outgoing_cue_ms=10_000,
            incoming_cue_ms=0,
            outgoing_bpm=120,
            incoming_bpm=120,
            outgoing_duration_ms=20_000,
            incoming_duration_ms=30_000,
            bars_per_square=8,
            square_count=1,
        )
        with self.assertRaises(SetTimelineError):
            build_transition_plan(**arguments)
        arguments["outgoing_duration_ms"] = 40_000
        arguments["incoming_bpm"] = None
        with self.assertRaises(SetTimelineError):
            build_transition_plan(**arguments)

    def test_playlist_timeline_alternates_lanes_and_aligns_cues(self) -> None:
        transitions = [
            TimelineTransition(1, 2, 40_000, 5_000, 16_000),
            TimelineTransition(2, 3, 45_000, 10_000, 16_000),
        ]
        layout = build_playlist_timeline(
            [(1, 60_000), (2, 70_000), (3, 80_000)], transitions
        )
        self.assertEqual([item.lane for item in layout.clips], [0, 1, 0])
        self.assertEqual([item.start_ms for item in layout.clips], [0, 35_000, 70_000])
        self.assertEqual(layout.transition_positions_ms, (40_000, 80_000))
        self.assertEqual(layout.duration_ms, 150_000)


class SetTimelineRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = LibraryDatabase(self.root / "library.sqlite3")
        self.database.initialize()
        self.playlists = PlaylistRepository(self.database)
        self.repository = SetTimelineRepository(self.database)
        self.track_ids: list[int] = []
        for index in range(3):
            path = self.root / f"track-{index}.wav"
            path.write_bytes(b"audio")
            self.database.upsert_track(
                path=path,
                root=self.root,
                size=5,
                mtime_ns=1,
                extension=".wav",
                file_hash=str(index),
                metadata=AudioMetadata(title=f"Track {index}", bpm=120 + index),
                technical=AudioTechnicalInfo(duration=60),
                scan_token="scan",
            )
            self.track_ids.append(
                self.database.list_tracks(search=str(path))[0].id
            )
        self.playlist_id = self.playlists.create_with_tracks(
            "Set", self.track_ids[:2]
        )

    def transition(self) -> SavedTransition:
        return SavedTransition(
            playlist_id=self.playlist_id,
            outgoing_track_id=self.track_ids[0],
            incoming_track_id=self.track_ids[1],
            outgoing_cue_ms=30_000,
            incoming_cue_ms=1_000,
            bars_per_square=8,
            square_count=2,
        )

    def test_cue_points_are_upserted_and_ordered(self) -> None:
        self.repository.save_cue_point(self.track_ids[0], 3, 9_000)
        self.repository.save_cue_point(self.track_ids[0], 1, 2_000)
        self.repository.save_cue_point(self.track_ids[0], 1, 2_500)
        self.assertEqual(
            [(item.slot, item.position_ms) for item in self.repository.cue_points(self.track_ids[0])],
            [(1, 2_500), (3, 9_000)],
        )
        batch = self.repository.cue_points_for_tracks(self.track_ids[:2])
        self.assertEqual([item.slot for item in batch[self.track_ids[0]]], [1, 3])
        self.assertEqual(batch[self.track_ids[1]], [])

    def test_transition_round_trip_and_membership_validation(self) -> None:
        transition = self.transition()
        self.repository.save_transition(transition)
        self.assertEqual(
            self.repository.transition(
                self.playlist_id, self.track_ids[0], self.track_ids[1]
            ),
            transition,
        )
        self.assertEqual(self.repository.transitions(self.playlist_id), [transition])
        invalid = SavedTransition(
            playlist_id=self.playlist_id,
            outgoing_track_id=self.track_ids[0],
            incoming_track_id=self.track_ids[2],
            outgoing_cue_ms=0,
            incoming_cue_ms=0,
            bars_per_square=8,
            square_count=1,
        )
        with self.assertRaises(SetTimelineRepositoryError):
            self.repository.save_transition(invalid)

    def test_track_and_playlist_deletion_cascade_editor_data(self) -> None:
        self.repository.save_cue_point(self.track_ids[0], 1, 2_000)
        self.repository.save_transition(self.transition())
        self.playlists.delete(self.playlist_id)
        self.assertIsNone(
            self.repository.transition(
                self.playlist_id, self.track_ids[0], self.track_ids[1]
            )
        )
        with self.database.connection() as conn:
            conn.execute("DELETE FROM tracks WHERE id=?", (self.track_ids[0],))
            conn.commit()
        self.assertEqual(self.repository.cue_points(self.track_ids[0]), [])

    def test_v5_database_migrates_without_losing_tracks(self) -> None:
        with self.database.connection() as conn:
            conn.execute("DROP TABLE playlist_transitions")
            conn.execute("DROP TABLE track_cue_points")
            conn.execute("PRAGMA user_version=5")
            conn.commit()
        self.database.initialize()
        with self.database.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 8)
        self.assertEqual(len(self.database.list_tracks()), 3)


class TransitionPreviewTests(unittest.TestCase):
    def plan(self):
        return build_transition_plan(
            outgoing_cue_ms=10_000,
            incoming_cue_ms=1_000,
            outgoing_bpm=120,
            incoming_bpm=125,
            outgoing_duration_ms=30_000,
            incoming_duration_ms=30_000,
            bars_per_square=4,
            square_count=1,
        )

    def test_command_contains_tempo_delay_fades_and_limiter(self) -> None:
        command = build_transition_preview_command(
            Path("ffmpeg"), Path("a.wav"), Path("b.wav"), Path("out.wav"), self.plan()
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("atempo=0.96000000", graph)
        self.assertIn("adelay=4000|4000", graph)
        self.assertIn("afade=t=out", graph)
        self.assertIn("afade=t=in", graph)
        self.assertIn("amix=inputs=2", graph)
        self.assertIn("alimiter=limit=0.95", graph)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg отсутствует")
    def test_real_ffmpeg_preview_has_expected_duration(self) -> None:
        ffmpeg = Path(shutil.which("ffmpeg") or "ffmpeg")
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            sources = [root / "a.wav", root / "b.wav"]
            for index, source in enumerate(sources):
                subprocess.run(
                    [
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"sine=frequency={440 + index * 220}:duration=30",
                        "-c:a", "pcm_s16le", str(source),
                    ],
                    check=True,
                )
            output = render_transition_preview(
                ffmpeg, sources[0], sources[1], root / "cache", self.plan()
            )
            self.assertTrue(output.is_file())
            with wave.open(str(output), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            self.assertAlmostEqual(duration, 16.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
