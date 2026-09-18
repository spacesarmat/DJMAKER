"""Плагин Apple Music: публичная страница поиска (без входа, без API-ключа).

Страница music.apple.com рендерится на сервере и содержит блок
``serialized-server-data`` с уже готовым JSON результатов поиска —
JS не нужен, читаем как обычную HTML-страницу.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import RateLimiter, extract_embedded_json, urlopen_with_retry


class AppleMusicProvider(MetadataProvider):
    """Провайдер публичной страницы поиска Apple Music."""

    provider_id = "apple_music"
    display_name = "Apple Music"
    _search_url = "https://music.apple.com/us/search"
    _marker = 'id="serialized-server-data"'

    def __init__(self) -> None:
        self._limiter = RateLimiter(0.5)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки на публичной странице поиска Apple Music."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        term = f"{artist} {title}".strip() if artist else title
        params = urllib.parse.urlencode({"term": term})
        url = f"{self._search_url}?{params}"

        self._limiter.wait()
        request = urllib.request.Request(
            url, headers={"Accept": "text/html", "User-Agent": self._user_agent()}
        )
        payload = urlopen_with_retry(request, provider_label="Apple Music")
        html = payload.decode("utf-8", errors="ignore")

        data = extract_embedded_json(html, self._marker)
        section = self._track_section(data)
        if section is None:
            return []
        items = section.get("items")
        if not isinstance(items, list):
            return []
        return [
            self._candidate(item)
            for item in items[: max(1, limit)]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _track_section(data: Any) -> dict[str, Any] | None:
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not entries:
            return None
        page = entries[0].get("data") if isinstance(entries[0], dict) else None
        sections = page.get("sections") if isinstance(page, dict) else None
        if not isinstance(sections, list):
            return None
        for section in sections:
            if isinstance(section, dict) and section.get("itemKind") == "trackLockup":
                return section
        return None

    def _candidate(self, item: dict[str, Any]) -> MetadataCandidate:
        title = str(item.get("title") or "")

        artist = ""
        subtitle_links = item.get("subtitleLinks")
        if isinstance(subtitle_links, list) and subtitle_links:
            first = subtitle_links[0]
            if isinstance(first, dict):
                artist = str(first.get("title") or "")

        descriptor = item.get("contentDescriptor")
        identifiers = descriptor.get("identifiers") if isinstance(descriptor, dict) else None
        external_id = str(identifiers.get("storeAdamID") or "") if isinstance(identifiers, dict) else ""

        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=external_id,
            title=title,
            artist=artist,
            artwork_url=self._artwork_url(item.get("artwork")),
        )

    @staticmethod
    def _artwork_url(artwork: Any) -> str:
        if not isinstance(artwork, dict):
            return ""
        dictionary = artwork.get("dictionary")
        template = dictionary.get("url") if isinstance(dictionary, dict) else None
        if not isinstance(template, str):
            return ""
        return template.replace("{w}", "1200").replace("{h}", "1200").replace("{f}", "jpg")

    @staticmethod
    def _user_agent() -> str:
        return "DJMAKER metadata search (+https://github.com/DJMAKER)"
