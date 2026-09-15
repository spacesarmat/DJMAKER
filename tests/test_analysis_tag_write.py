from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mutagen.easymp4 import EasyMP4Tags
from mutagen.id3 import ID3, TBPM, TKEY
from mutagen.mp4 import MP4, MP4FreeForm, MP4Tags

from djmaker.domain.models import (
    AudioAnalysis,
    AudioMetadata,
    AudioTechnicalInfo,
    InspectedAudio,
    TrackRecord,
)
from djmaker.services.audio_tags import AudioTagService
from djmaker.services.library import LibraryService


class AnalysisTagWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = AudioTagService()

    def test_analysis_key_tag_value_includes_scale(self) -> None:
        analysis = AudioAnalysis(musical_key="A", scale="minor", camelot="8A")
        self.assertEqual("A minor", self.tags.analysis_key_tag_value(analysis))

    def test_easy_mp4_reads_registered_initialkey_freeform_tag(self) -> None:
        self.assertIn("initialkey", EasyMP4Tags.Get)

    def test_id3_analysis_write_replaces_only_available_analysis_tags(self) -> None:
        id3 = ID3()
        id3.add(TBPM(encoding=3, text=["120"]))
        id3.add(TKEY(encoding=3, text=["C major"]))
        audio = SimpleNamespace(tags=id3, save=Mock())
        analysis = AudioAnalysis(
            bpm=127.98,
            musical_key="A",
            scale="minor",
            camelot="8A",
        )

        with patch("djmaker.services.audio_tags.File", return_value=audio):
            self.tags.write_analysis(Path("track.mp3"), analysis)

        self.assertEqual(["127.98"], id3.getall("TBPM")[0].text)
        self.assertEqual(["A minor"], id3.getall("TKEY")[0].text)
        audio.save.assert_called_once_with()

    def test_missing_analysis_value_does_not_delete_existing_id3_tag(self) -> None:
        id3 = ID3()
        id3.add(TBPM(encoding=3, text=["124"]))
        audio = SimpleNamespace(tags=id3, save=Mock())
        analysis = AudioAnalysis(musical_key="F#", scale="minor")

        with patch("djmaker.services.audio_tags.File", return_value=audio):
            self.tags.write_analysis(Path("track.mp3"), analysis)

        self.assertEqual(["124"], id3.getall("TBPM")[0].text)
        self.assertEqual(["F# minor"], id3.getall("TKEY")[0].text)

    def test_mp4_analysis_uses_tmpo_and_initialkey(self) -> None:
        audio = MP4()
        audio.tags = MP4Tags()
        analysis = AudioAnalysis(
            bpm=127.6,
            musical_key="G",
            scale="major",
            camelot="9B",
        )

        with (
            patch("djmaker.services.audio_tags.File", return_value=audio),
            patch.object(MP4, "save") as save,
        ):
            self.tags.write_analysis(Path("track.m4a"), analysis)

        self.assertEqual([128], audio.tags["tmpo"])
        key = audio.tags["----:com.apple.iTunes:INITIALKEY"][0]
        self.assertIsInstance(key, MP4FreeForm)
        self.assertEqual(b"G major", bytes(key))
        save.assert_called_once_with()

    def test_mapping_analysis_write_uses_vorbis_names(self) -> None:
        audio = SimpleNamespace(tags={}, save=Mock())

        self.tags._write_analysis_mapping(
            audio,
            126.25,
            "D minor",
            ape_style=False,
        )

        self.assertEqual("126.25", audio.tags["bpm"])
        self.assertEqual("D minor", audio.tags["initialkey"])
        audio.save.assert_called_once_with()


class LibraryAnalysisTagSyncTests(unittest.TestCase):
    def test_analysis_writes_tags_before_database_analysis_and_syncs_hash(self) -> None:
        track_path = Path("/music/track.flac")
        track = TrackRecord(
            id=7,
            path=track_path,
            root_path=Path("/music"),
            size=100,
            mtime_ns=1,
            extension=".flac",
            file_hash="old",
            metadata=AudioMetadata(title="Track"),
            technical=AudioTechnicalInfo(duration=180.0),
        )
        updated = TrackRecord(
            id=7,
            path=track_path,
            root_path=Path("/music"),
            size=101,
            mtime_ns=2,
            extension=".flac",
            file_hash="new",
            metadata=AudioMetadata(
                title="Track",
                bpm=127.98,
                musical_key="A minor",
            ),
            technical=AudioTechnicalInfo(duration=180.0),
        )
        analysis = AudioAnalysis(
            bpm=127.98,
            musical_key="A",
            scale="minor",
            camelot="8A",
        )
        inspected = InspectedAudio(
            path=track_path,
            metadata=AudioMetadata(title="Track"),
            technical=AudioTechnicalInfo(duration=180.0),
        )
        stat = SimpleNamespace(st_size=101, st_mtime_ns=2)
        timeline: list[str] = []

        database = Mock()
        database.get_track.side_effect = [track, updated]
        database.update_after_file_change.side_effect = lambda *a, **k: timeline.append(
            "database-file"
        )
        database.save_audio_analysis.side_effect = lambda *a, **k: timeline.append(
            "database-analysis"
        )
        scanner = Mock()
        scanner.inspect_and_hash.side_effect = lambda *a, **k: (
            timeline.append("inspect") or (inspected, "new", stat)
        )
        tags = AudioTagService()
        tags.write_analysis = Mock(side_effect=lambda *a, **k: timeline.append("tags"))
        analyzer = Mock()
        analyzer.analyze.return_value = analysis

        service = LibraryService(
            database=database,
            scanner=scanner,
            tags=tags,
            organizer=Mock(),
            plugins=Mock(),
            analyzer=analyzer,
            artwork_cache=Mock(),
            waveform_analyzer=Mock(),
        )

        result = service.analyze_track(7)

        self.assertIs(updated, result)
        self.assertEqual(
            ["tags", "inspect", "database-file", "database-analysis"],
            timeline,
        )
        self.assertAlmostEqual(127.98, inspected.metadata.bpm or 0.0)
        self.assertEqual("A minor", inspected.metadata.musical_key)
        database.update_after_file_change.assert_called_once()
        self.assertEqual(
            "new",
            database.update_after_file_change.call_args.kwargs["file_hash"],
        )
        database.save_audio_analysis.assert_called_once_with(7, analysis)


if __name__ == "__main__":
    unittest.main()
