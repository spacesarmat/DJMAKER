"""Подбор продолжения сета по BPM и Camelot."""

from __future__ import annotations

import unittest
from pathlib import Path

from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    TrackRecord,
)
from djmaker.domain.set_planner import (
    camelot_code,
    recommend_tracks,
    track_bpm,
    track_camelot_order,
)


def track(
    ident: int,
    *,
    bpm: float | None = None,
    key: str = "",
    analysis_bpm: float | None = None,
    analysis_camelot: str = "",
    artist: str = "Artist",
) -> TrackRecord:
    analysis = None
    if analysis_bpm is not None or analysis_camelot:
        analysis = AudioAnalysis(bpm=analysis_bpm, camelot=analysis_camelot)
    return TrackRecord(
        id=ident,
        path=Path(f"{ident}.flac"),
        root_path=Path("."),
        size=1,
        mtime_ns=1,
        extension=".flac",
        file_hash=str(ident),
        metadata=AudioMetadata(
            title=f"Track {ident}", artist=artist, bpm=bpm, musical_key=key
        ),
        technical=AudioTechnicalInfo(duration=180),
        analysis=analysis,
    )


class SetPlannerTests(unittest.TestCase):
    def test_dsp_values_have_priority_over_file_tags(self) -> None:
        item = track(
            1,
            bpm=90,
            key="1A",
            analysis_bpm=128,
            analysis_camelot="8A",
        )
        self.assertEqual(track_bpm(item), 128)
        self.assertEqual(camelot_code(track_camelot_order(item)), "8A")

    def test_strict_accepts_exact_neighbor_and_parallel_keys(self) -> None:
        reference = track(1, bpm=128, key="8A")
        candidates = [
            track(2, bpm=128, key="8A"),
            track(3, bpm=130, key="9A"),
            track(4, bpm=126, key="8B"),
            track(5, bpm=128, key="10A"),
            track(6, bpm=140, key="8A"),
        ]
        result = recommend_tracks(reference, candidates, mode="strict")
        self.assertEqual([item.track.id for item in result], [2, 3, 4])
        self.assertIn("та же тональность", result[0].reason)
        self.assertIn("соседняя тональность", result[1].reason)
        self.assertIn("параллельный лад", result[2].reason)

    def test_balanced_accepts_energy_transition_and_unknown_key(self) -> None:
        reference = track(1, bpm=120, key="8A")
        result = recommend_tracks(
            reference,
            [
                track(2, bpm=125, key="10A"),
                track(3, bpm=121),
                track(4, bpm=129, key="8A"),
            ],
            mode="balanced",
        )
        self.assertEqual([item.track.id for item in result], [2, 3])
        self.assertIn("энергетический переход", result[0].reason)
        self.assertIn("тональность неизвестна", result[1].reason)

    def test_half_and_double_bpm_are_matched(self) -> None:
        reference = track(1, bpm=128, key="8A")
        result = recommend_tracks(
            reference,
            [track(2, bpm=64, key="8A"), track(3, bpm=256, key="8A")],
            mode="strict",
        )
        self.assertEqual([item.track.id for item in result], [2, 3])
        self.assertEqual({item.bpm_transform for item in result}, {"×2", "÷2"})
        self.assertTrue(all(item.bpm_difference_percent == 0 for item in result))

    def test_free_mode_can_use_harmony_when_bpm_is_unknown(self) -> None:
        reference = track(1, key="12B")
        result = recommend_tracks(
            reference,
            [track(2, key="1B"), track(3, key="6A"), track(4)],
            mode="free",
        )
        self.assertEqual([item.track.id for item in result], [2])

    def test_playlist_members_reference_and_explicit_exclusions_are_removed(self) -> None:
        reference = track(1, bpm=128, key="8A")
        result = recommend_tracks(
            reference,
            [reference, track(2, bpm=128, key="8A"), track(3, bpm=128, key="8A")],
            excluded_track_ids={2},
        )
        self.assertEqual([item.track.id for item in result], [3])

    def test_invalid_mode_falls_back_limit_is_respected_and_order_is_stable(self) -> None:
        reference = track(1, bpm=128, key="8A")
        candidates = [
            track(4, bpm=128, key="8A", artist="b"),
            track(3, bpm=128, key="8A", artist="A"),
            track(2, bpm=128, key="8A", artist="a"),
        ]
        result = recommend_tracks(reference, candidates, mode="invalid", limit=2)
        self.assertEqual([item.track.id for item in result], [2, 3])

    def test_camelot_wraps_between_twelve_and_one(self) -> None:
        reference = track(1, bpm=128, key="12A")
        result = recommend_tracks(
            reference, [track(2, bpm=128, key="1A")], mode="strict"
        )
        self.assertEqual(len(result), 1)
        self.assertIn("соседняя тональность", result[0].reason)

    def test_source_records_are_not_modified(self) -> None:
        reference = track(1, bpm=128, key="8A")
        candidate = track(2, bpm=129, key="9A")
        before = (candidate.metadata.bpm, candidate.metadata.musical_key)
        recommend_tracks(reference, [candidate])
        self.assertEqual(
            (candidate.metadata.bpm, candidate.metadata.musical_key), before
        )


if __name__ == "__main__":
    unittest.main()
