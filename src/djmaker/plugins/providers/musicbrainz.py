"""Плагин MusicBrainz: поиск метаданных без стороннего HTTP-клиента."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from djmaker import __version__
from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import (
    RateLimiter,
    is_server_error,
    retry_after_seconds,
    urlopen_with_retry,
)


class MusicBrainzProvider(MetadataProvider):
    """Провайдер MusicBrainz с простым ограничением частоты запросов."""

    provider_id = "musicbrainz"
    display_name = "MusicBrainz"
    _base_url = "https://musicbrainz.org/ws/2/recording/"

    def __init__(self) -> None:
        self._limiter = RateLimiter(1.05)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет записи MusicBrainz по title/artist локального трека."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        query_parts = [f'recording:"{self._escape_query(title)}"']
        if artist:
            query_parts.append(f'artist:"{self._escape_query(artist)}"')
        query = " AND ".join(query_parts)
        params = urllib.parse.urlencode(
            {
                "query": query,
                "fmt": "json",
                "limit": max(1, min(limit, 25)),
                "inc": "tags",
            }
        )
        url = f"{self._base_url}?{params}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self._user_agent(),
            },
        )
        payload = self._request(request)

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetadataProviderError(f"Некорректный ответ MusicBrainz: {exc}") from exc

        recordings = data.get("recordings", [])
        if not isinstance(recordings, list):
            raise MetadataProviderError("MusicBrainz вернул неожиданный формат данных")
        return [self._candidate(item) for item in recordings if isinstance(item, dict)]

    def _candidate(self, item: dict[str, Any]) -> MetadataCandidate:
        artists = item.get("artist-credit") or []
        artist = ""
        if isinstance(artists, list):
            names: list[str] = []
            for credit in artists:
                if isinstance(credit, dict):
                    name = credit.get("name")
                    if isinstance(name, str) and name:
                        names.append(name)
            artist = " & ".join(names)

        album = ""
        year = ""
        release_id = ""
        releases = item.get("releases") or []
        if isinstance(releases, list) and releases:
            release = releases[0]
            if isinstance(release, dict):
                album = str(release.get("title") or "")
                year = str(release.get("date") or "")[:4]
                release_id = str(release.get("id") or "")

        artwork_url = (
            f"https://coverartarchive.org/release/{release_id}/front-500"
            if release_id
            else ""
        )
        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            artist=artist,
            album=album,
            year=year,
            release_id=release_id,
            artwork_url=artwork_url,
            genre=self._top_tag(item.get("tags")),
        )

    @staticmethod
    def _top_tag(tags: Any) -> str:
        if not isinstance(tags, list):
            return ""
        named = [
            (str(tag.get("name") or ""), int(tag.get("count") or 0))
            for tag in tags
            if isinstance(tag, dict) and tag.get("name")
        ]
        if not named:
            return ""
        named.sort(key=lambda pair: pair[1], reverse=True)
        return named[0][0]

    def _request(self, request: urllib.request.Request, *, retried: bool = False) -> bytes:
        self._limiter.wait()
        try:
            return urlopen_with_retry(request, provider_label="MusicBrainz")
        except urllib.error.HTTPError as exc:
            if is_server_error(exc) and not retried:
                time.sleep(retry_after_seconds(exc))
                return self._request(request, retried=True)
            raise MetadataProviderError(f"MusicBrainz недоступен: {exc}") from exc

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _user_agent() -> str:
        contact = os.getenv("DJMAKER_MUSICBRAINZ_CONTACT", "https://github.com/DJMAKER")
        return f"DJMAKER/{__version__} ({contact})"
