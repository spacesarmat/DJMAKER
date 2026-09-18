"""Плагин Deezer: публичный Search API, не требует ключа/авторизации."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import RateLimiter, urlopen_with_retry


class DeezerProvider(MetadataProvider):
    """Провайдер Deezer Search API (публичный, без ключа)."""

    provider_id = "deezer"
    display_name = "Deezer"
    _search_url = "https://api.deezer.com/search"

    def __init__(self) -> None:
        self._limiter = RateLimiter(0.2)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки Deezer по title/artist локального трека."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        query = f'track:"{self._escape_query(title)}"'
        if artist:
            query += f' artist:"{self._escape_query(artist)}"'
        params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 25))})
        url = f"{self._search_url}?{params}"

        self._limiter.wait()
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        payload = urlopen_with_retry(request, provider_label="Deezer")

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetadataProviderError(f"Некорректный ответ Deezer: {exc}") from exc

        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            error = data["error"]
            raise MetadataProviderError(
                f"Deezer вернул ошибку: {error.get('message') or error}"
            )

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise MetadataProviderError("Deezer вернул неожиданный формат данных")
        return [self._candidate(item) for item in items if isinstance(item, dict)]

    def _candidate(self, item: dict[str, Any]) -> MetadataCandidate:
        artist = item.get("artist") if isinstance(item.get("artist"), dict) else {}
        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        artwork_url = str(
            album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or ""
        )
        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            artist=str(artist.get("name") or ""),
            album=str(album.get("title") or ""),
            release_id=str(album.get("id") or ""),
            artwork_url=artwork_url,
        )

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', "'")
