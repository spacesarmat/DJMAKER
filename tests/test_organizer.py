from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from djmaker.domain.models import AudioMetadata, AudioTechnicalInfo, TrackRecord
from djmaker.services.organizer import FileOrganizer, OrganizerError


class FileOrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.organizer = FileOrganizer()

    def test_build_target_is_cross_platform_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp3"
            source.write_bytes(b"data")
            track = TrackRecord(
                id=1,
                path=source,
                root_path=root,
                size=4,
                mtime_ns=1,
                extension=".mp3",
                file_hash="x",
                metadata=AudioMetadata(
                    artist='AC/DC',
                    album='Best: Hits?',
                    title='Track * One',
                    track_number=3,
                ),
                technical=AudioTechnicalInfo(),
            )

            target = self.organizer.build_target(
                track,
                root / "library",
                "{artist}/{album}/{track:02d} - {title}.{ext}",
            )

            expected = (
                root
                / "library"
                / "AC_DC"
                / "Best_ Hits_"
                / "03 - Track _ One.mp3"
            ).resolve()
            self.assertEqual(expected, target)

    def test_unknown_template_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp3"
            source.write_bytes(b"data")
            track = TrackRecord(
                id=1,
                path=source,
                root_path=root,
                size=4,
                mtime_ns=1,
                extension=".mp3",
                file_hash="x",
                metadata=AudioMetadata(),
                technical=AudioTechnicalInfo(),
            )
            with self.assertRaises(OrganizerError):
                self.organizer.build_target(track, root, "{unknown}.{ext}")

    def test_move_never_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp3"
            target = root / "target.mp3"
            source.write_bytes(b"source")
            target.write_bytes(b"existing")

            moved = self.organizer.move(source, target)

            self.assertEqual(b"existing", target.read_bytes())
            self.assertEqual(b"source", moved.read_bytes())
            self.assertNotEqual(target, moved)


if __name__ == "__main__":
    unittest.main()
