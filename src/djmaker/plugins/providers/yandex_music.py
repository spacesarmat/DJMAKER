"""Плагин Яндекс Музыка: публичная страница поиска (без входа, без API-ключа).

music.yandex.ru стримит результаты поиска через несколько <script>-тегов как
JSON Patch операции (React Server Components), а не одним блоком — поэтому
здесь используется collect_react_state_patches вместо extract_embedded_json.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import (
    RateLimiter,
    collect_react_state_patches,
    urlopen_with_retry,
)

_ITEM_PATH_RE = re.compile(r"/search/pagesLoader/items/(\d+)")


class YandexMusicProvider(MetadataProvider):
    """Провайдер публичной страницы поиска Яндекс Музыки."""

    provider_id = "yandex_music"
    display_name = "Яндекс Музыка"
    _search_url = "https://music.yandex.ru/search"

    def __init__(self) -> None:
        self._limiter = RateLimiter(0.5)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки на публичной странице поиска Яндекс Музыки."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        text = f"{artist} {title}".strip() if artist else title
        params = urllib.parse.urlencode({"text": text})
        url = f"{self._search_url}?{params}"

        self._limiter.wait()
        request = urllib.request.Request(
            url, headers={"Accept": "text/html", "User-Agent": self._user_agent()}
        )
        payload = urlopen_with_retry(request, provider_label="Яндекс Музыка")
        html = payload.decode("utf-8", errors="ignore")

        items = collect_react_state_patches(html, _ITEM_PATH_RE)
        if not items:
            raise MetadataProviderError("Яндекс Музыка: не удалось разобрать страницу поиска")

        tracks = [
            value.get("data")
            for value in items.values()
            if isinstance(value, dict) and value.get("type") == "track"
        ]
        return [
            self._candidate(item)
            for item in tracks[: max(1, limit)]
            if isinstance(item, dict)
        ]

    def _candidate(self, item: dict[str, Any]) -> MetadataCandidate:
        artists = item.get("artists")
        artist_names: list[str] = []
        if isinstance(artists, list):
            for entry in artists:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    if isinstance(name, str) and name:
                        artist_names.append(name)

        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=str(item.get("id") or ""),
            title=str(item.get("title") or ""),
            artist=" & ".join(artist_names),
            artwork_url=self._artwork_url(item.get("coverUri")),
            genre=str(item.get("genre") or ""),
        )

    @staticmethod
    def _artwork_url(cover_uri: Any) -> str:
        if not isinstance(cover_uri, str) or not cover_uri:
            return ""
        return f"https://{cover_uri}".replace("%%", "400x400")

    @staticmethod
    def _user_agent() -> str:
        return "DJMAKER metadata search (+https://github.com/DJMAKER)"
