"""Чтение и запись аудиотегов через Mutagen."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mutagen import File, MutagenError
from mutagen.apev2 import APEv2File
from mutagen.asf import ASF
from mutagen.id3 import (
    ID3,
    TALB,
    TBPM,
    TCON,
    TDRC,
    TIT2,
    TKEY,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.ogg import OggFileType
from mutagen.flac import FLAC

from djmaker.domain.models import (
    AudioMetadata,
    AudioTechnicalInfo,
    EmbeddedArtwork,
    InspectedAudio,
)


LOGGER = logging.getLogger(__name__)


class AudioTagError(RuntimeError):
    """Ошибка чтения или записи аудиометаданных."""


class UnsupportedTagWriteError(AudioTagError):
    """Формат можно индексировать, но запись тегов для него пока не реализована."""


class AudioTagService:
    """Читает технические свойства и редактирует основные музыкальные теги."""

    def inspect(self, path: Path) -> InspectedAudio:
        """Читает метаданные и технические параметры аудиофайла."""
        try:
            audio = File(path, easy=True)
        except (MutagenError, OSError) as exc:
            raise AudioTagError(f"Не удалось прочитать {path}: {exc}") from exc

        if audio is None:
            raise AudioTagError(f"Формат файла не распознан: {path}")

        tags = audio.tags or {}
        info = getattr(audio, "info", None)
        metadata = AudioMetadata(
            title=self._first(tags, "title"),
            artist=self._first(tags, "artist"),
            album=self._first(tags, "album"),
            album_artist=self._first(tags, "albumartist", "album artist"),
            genre=self._first(tags, "genre"),
            year=self._first(tags, "date", "year"),
            track_number=self._parse_index(self._first(tags, "tracknumber", "track")),
            disc_number=self._parse_index(self._first(tags, "discnumber", "disc")),
            bpm=self._parse_float(self._first(tags, "bpm")),
            musical_key=self._first(tags, "initialkey", "key"),
        )
        technical = AudioTechnicalInfo(
            duration=self._number_attr(info, "length", float),
            bitrate=self._number_attr(info, "bitrate", int),
            sample_rate=self._number_attr(info, "sample_rate", int),
            channels=self._number_attr(info, "channels", int),
        )
        return InspectedAudio(
            path=path,
            metadata=metadata,
            technical=technical,
            artwork=self.embedded_artwork(path),
        )

    def embedded_artwork(self, path: Path) -> EmbeddedArtwork | None:
        """Читает встроенную обложку MP3/FLAC/M4A, не декодируя аудио."""
        suffix = path.suffix.lower()
        if suffix not in {".mp3", ".flac", ".m4a", ".m4b", ".mp4"}:
            return None

        try:
            audio = File(path)
        except (MutagenError, OSError) as exc:
            LOGGER.warning("Не удалось прочитать встроенную обложку %s: %s", path, exc)
            return None
        if audio is None:
            return None

        try:
            if suffix == ".mp3":
                return self._id3_artwork(getattr(audio, "tags", None))
            if suffix == ".flac":
                return self._flac_artwork(audio)
            return self._mp4_artwork(getattr(audio, "tags", None))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            LOGGER.warning("Некорректная встроенная обложка %s: %s", path, exc)
            return None

    @staticmethod
    def _id3_artwork(tags: Any) -> EmbeddedArtwork | None:
        if tags is None or not hasattr(tags, "getall"):
            return None
        frames = list(tags.getall("APIC"))
        if not frames:
            return None
        frame = next(
            (item for item in frames if int(getattr(item, "type", 0)) == 3),
            frames[0],
        )
        data = bytes(getattr(frame, "data", b""))
        if not data:
            return None
        return EmbeddedArtwork(
            data=data,
            mime_type=str(getattr(frame, "mime", "") or ""),
        )

    @staticmethod
    def _flac_artwork(audio: Any) -> EmbeddedArtwork | None:
        pictures = list(getattr(audio, "pictures", ()) or ())
        if not pictures:
            return None
        picture = next(
            (item for item in pictures if int(getattr(item, "type", 0)) == 3),
            pictures[0],
        )
        data = bytes(getattr(picture, "data", b""))
        if not data:
            return None
        return EmbeddedArtwork(
            data=data,
            mime_type=str(getattr(picture, "mime", "") or ""),
        )

    @staticmethod
    def _mp4_artwork(tags: Any) -> EmbeddedArtwork | None:
        if tags is None:
            return None
        covers = tags.get("covr") or []
        if not covers:
            return None
        cover = covers[0]
        data = bytes(cover)
        if not data:
            return None
        image_format = getattr(cover, "imageformat", None)
        if image_format == MP4Cover.FORMAT_PNG:
            mime_type = "image/png"
        elif image_format == MP4Cover.FORMAT_JPEG:
            mime_type = "image/jpeg"
        else:
            mime_type = ""
        return EmbeddedArtwork(data=data, mime_type=mime_type)

    def write(self, path: Path, metadata: AudioMetadata) -> None:
        """Записывает основные теги, используя нативный формат контейнера."""
        try:
            audio = File(path)
        except (MutagenError, OSError) as exc:
            raise AudioTagError(f"Не удалось открыть {path} для записи: {exc}") from exc

        if audio is None:
            raise AudioTagError(f"Формат файла не распознан: {path}")

        try:
            if isinstance(audio, MP4):
                self._write_mp4(audio, metadata)
            elif isinstance(audio, ASF):
                self._write_asf(audio, metadata)
            elif isinstance(audio, (FLAC, OggFileType)):
                self._write_mapping(audio, metadata, ape_style=False)
            elif isinstance(audio, APEv2File):
                self._write_mapping(audio, metadata, ape_style=True)
            elif isinstance(getattr(audio, "tags", None), ID3):
                self._write_id3(audio, metadata)
            else:
                self._ensure_tags(audio)
                if isinstance(getattr(audio, "tags", None), ID3):
                    self._write_id3(audio, metadata)
                else:
                    raise UnsupportedTagWriteError(
                        f"Запись тегов для {type(audio).__name__} пока не реализована"
                    )
        except UnsupportedTagWriteError:
            raise
        except (MutagenError, OSError, KeyError, ValueError, TypeError) as exc:
            raise AudioTagError(f"Не удалось записать теги {path}: {exc}") from exc

    @staticmethod
    def _ensure_tags(audio: Any) -> None:
        if getattr(audio, "tags", None) is None:
            audio.add_tags()

    def _write_mp4(self, audio: MP4, metadata: AudioMetadata) -> None:
        self._ensure_tags(audio)
        tags = audio.tags
        if tags is None:
            raise AudioTagError("MP4-контейнер не создал таблицу тегов")

        self._set_or_delete(tags, "\xa9nam", [metadata.title])
        self._set_or_delete(tags, "\xa9ART", [metadata.artist])
        self._set_or_delete(tags, "\xa9alb", [metadata.album])
        self._set_or_delete(tags, "aART", [metadata.album_artist])
        self._set_or_delete(tags, "\xa9gen", [metadata.genre])
        self._set_or_delete(tags, "\xa9day", [metadata.year])
        self._set_or_delete(
            tags,
            "trkn",
            [(metadata.track_number, 0)] if metadata.track_number is not None else None,
        )
        self._set_or_delete(
            tags,
            "disk",
            [(metadata.disc_number, 0)] if metadata.disc_number is not None else None,
        )
        self._set_or_delete(
            tags,
            "tmpo",
            [int(round(metadata.bpm))] if metadata.bpm is not None else None,
        )
        key_name = "----:com.apple.iTunes:INITIALKEY"
        key_value = (
            [MP4FreeForm(metadata.musical_key.encode("utf-8"))]
            if metadata.musical_key
            else None
        )
        self._set_or_delete(tags, key_name, key_value)
        audio.save()

    def _write_asf(self, audio: ASF, metadata: AudioMetadata) -> None:
        self._ensure_tags(audio)
        tags = audio.tags
        if tags is None:
            raise AudioTagError("ASF-контейнер не создал таблицу тегов")
        mapping: dict[str, str | None] = {
            "Title": metadata.title or None,
            "Author": metadata.artist or None,
            "WM/AlbumTitle": metadata.album or None,
            "WM/AlbumArtist": metadata.album_artist or None,
            "WM/Genre": metadata.genre or None,
            "WM/Year": metadata.year or None,
            "WM/TrackNumber": self._optional_str(metadata.track_number),
            "WM/PartOfSet": self._optional_str(metadata.disc_number),
            "WM/BeatsPerMinute": self._optional_number(metadata.bpm),
            "WM/InitialKey": metadata.musical_key or None,
        }
        for key, value in mapping.items():
            self._set_or_delete(tags, key, [value] if value is not None else None)
        audio.save()

    def _write_mapping(
        self, audio: Any, metadata: AudioMetadata, *, ape_style: bool
    ) -> None:
        self._ensure_tags(audio)
        tags = audio.tags
        if tags is None:
            raise AudioTagError("Контейнер не создал таблицу тегов")

        names = {
            "title": "Title" if ape_style else "title",
            "artist": "Artist" if ape_style else "artist",
            "album": "Album" if ape_style else "album",
            "album_artist": "Album Artist" if ape_style else "albumartist",
            "genre": "Genre" if ape_style else "genre",
            "year": "Year" if ape_style else "date",
            "track": "Track" if ape_style else "tracknumber",
            "disc": "Disc" if ape_style else "discnumber",
            "bpm": "BPM" if ape_style else "bpm",
            "key": "InitialKey" if ape_style else "initialkey",
        }
        values: dict[str, str | None] = {
            names["title"]: metadata.title or None,
            names["artist"]: metadata.artist or None,
            names["album"]: metadata.album or None,
            names["album_artist"]: metadata.album_artist or None,
            names["genre"]: metadata.genre or None,
            names["year"]: metadata.year or None,
            names["track"]: self._optional_str(metadata.track_number),
            names["disc"]: self._optional_str(metadata.disc_number),
            names["bpm"]: self._optional_number(metadata.bpm),
            names["key"]: metadata.musical_key or None,
        }
        for key, value in values.items():
            self._set_or_delete(tags, key, value)
        audio.save()

    def _write_id3(self, audio: Any, metadata: AudioMetadata) -> None:
        self._ensure_tags(audio)
        tags = audio.tags
        if not isinstance(tags, ID3):
            raise AudioTagError("Ожидались ID3-теги")

        self._replace_id3(tags, "TIT2", TIT2, metadata.title)
        self._replace_id3(tags, "TPE1", TPE1, metadata.artist)
        self._replace_id3(tags, "TALB", TALB, metadata.album)
        self._replace_id3(tags, "TPE2", TPE2, metadata.album_artist)
        self._replace_id3(tags, "TCON", TCON, metadata.genre)
        self._replace_id3(tags, "TDRC", TDRC, metadata.year)
        self._replace_id3(tags, "TRCK", TRCK, self._optional_str(metadata.track_number) or "")
        self._replace_id3(tags, "TPOS", TPOS, self._optional_str(metadata.disc_number) or "")
        self._replace_id3(tags, "TBPM", TBPM, self._optional_number(metadata.bpm) or "")
        self._replace_id3(tags, "TKEY", TKEY, metadata.musical_key)
        audio.save()

    @staticmethod
    def _replace_id3(tags: ID3, key: str, frame_type: Any, value: str) -> None:
        tags.delall(key)
        if value:
            tags.add(frame_type(encoding=3, text=[value]))

    @staticmethod
    def _set_or_delete(tags: Any, key: str, value: Any) -> None:
        if value is None or value == "" or value == []:
            try:
                del tags[key]
            except KeyError:
                return
            return
        tags[key] = value

    @staticmethod
    def _first(tags: Any, *keys: str) -> str:
        for key in keys:
            try:
                value = tags.get(key)
            except (AttributeError, KeyError, TypeError):
                value = None
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                if not value:
                    continue
                value = value[0]
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _parse_index(value: str) -> int | None:
        if not value:
            return None
        head = value.split("/", 1)[0].strip()
        try:
            return int(head)
        except ValueError:
            return None

    @staticmethod
    def _parse_float(value: str) -> float | None:
        if not value:
            return None
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _number_attr(info: Any, name: str, caster: Any) -> Any:
        if info is None:
            return None
        value = getattr(info, name, None)
        if value is None:
            return None
        try:
            return caster(value)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _optional_str(value: int | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _optional_number(value: float | None) -> str | None:
        if value is None:
            return None
        return f"{value:g}"
