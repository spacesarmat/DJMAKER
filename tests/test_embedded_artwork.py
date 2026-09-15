from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mutagen.mp4 import MP4Cover

from djmaker.domain.models import EmbeddedArtwork
from djmaker.services.artwork import ArtworkCache
from djmaker.services.audio_tags import AudioTagService


class _ID3Tags:
    def __init__(self, frames: list[object]) -> None:
        self.frames = frames

    def getall(self, key: str) -> list[object]:
        return self.frames if key == "APIC" else []


class EmbeddedArtworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = AudioTagService()

    def test_mp3_prefers_front_cover_apic(self) -> None:
        back = SimpleNamespace(type=4, mime="image/png", data=b"back")
        front = SimpleNamespace(type=3, mime="image/jpeg", data=b"front")
        audio = SimpleNamespace(tags=_ID3Tags([back, front]))

        with patch("djmaker.services.audio_tags.File", return_value=audio):
            artwork = self.tags.embedded_artwork(Path("track.mp3"))

        self.assertEqual(EmbeddedArtwork(b"front", "image/jpeg"), artwork)

    def test_flac_reads_front_picture(self) -> None:
        icon = SimpleNamespace(type=1, mime="image/png", data=b"icon")
        front = SimpleNamespace(type=3, mime="image/png", data=b"front-png")
        audio = SimpleNamespace(pictures=[icon, front])

        with patch("djmaker.services.audio_tags.File", return_value=audio):
            artwork = self.tags.embedded_artwork(Path("track.flac"))

        self.assertEqual(EmbeddedArtwork(b"front-png", "image/png"), artwork)

    def test_m4a_reads_covr_and_detects_png(self) -> None:
        cover = MP4Cover(
            b"\x89PNG\r\n\x1a\ncover",
            imageformat=MP4Cover.FORMAT_PNG,
        )
        audio = SimpleNamespace(tags={"covr": [cover]})

        with patch("djmaker.services.audio_tags.File", return_value=audio):
            artwork = self.tags.embedded_artwork(Path("track.m4a"))

        self.assertIsNotNone(artwork)
        assert artwork is not None
        self.assertEqual("image/png", artwork.mime_type)
        self.assertEqual(bytes(cover), artwork.data)

    def test_artwork_cache_is_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = ArtworkCache(Path(temp))
            artwork = EmbeddedArtwork(b"\xff\xd8\xffcover", "image/jpeg")

            first = cache.store(artwork)
            second = cache.store(artwork)

            self.assertEqual(first, second)
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(".jpg", first.suffix)
            self.assertEqual(artwork.data, first.read_bytes())


    def test_artwork_cache_handles_parallel_writes_of_same_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = ArtworkCache(Path(temp))
            artwork = EmbeddedArtwork(b"\xff\xd8\xffsame-cover", "image/jpeg")

            with ThreadPoolExecutor(max_workers=4) as executor:
                paths = list(executor.map(cache.store, [artwork] * 8))

            self.assertEqual(1, len(set(paths)))
            self.assertEqual(1, len(list(Path(temp).glob("*.jpg"))))

    def test_artwork_cache_serializes_replace_on_windows_style_race(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = ArtworkCache(Path(temp))
            artwork = EmbeddedArtwork(b"\xff\xd8\xffsame-cover", "image/jpeg")
            original_replace = Path.replace
            replace_active = False
            replace_guard = threading.Lock()

            def windows_like_replace(source: Path, target: Path) -> Path:
                nonlocal replace_active
                with replace_guard:
                    if replace_active:
                        raise PermissionError(5, "simulated Windows replace race")
                    replace_active = True
                try:
                    time.sleep(0.03)
                    return original_replace(source, target)
                finally:
                    with replace_guard:
                        replace_active = False

            with patch.object(Path, "replace", new=windows_like_replace):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    paths = list(executor.map(cache.store, [artwork] * 8))

            self.assertEqual(1, len(set(paths)))
            self.assertEqual(1, len(list(Path(temp).glob("*.jpg"))))
            self.assertEqual([], list(Path(temp).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
