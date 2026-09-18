"""Плагин Beatport: публичная страница поиска (без входа, без API-ключа).

Страница beatport.com/search рендерится на Next.js и содержит блок
``__NEXT_DATA__`` с уже готовым react-query кэшем результатов поиска —
включая BPM и тональность, которых нет у большинства других источников.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import RateLimiter, extract_embedded_json, urlopen_with_retry


class BeatportProvider(MetadataProvider):
    """Провайдер публичной страницы поиска Beatport."""

    provider_id = "beatport"
    display_name = "Beatport"
    _search_url = "https://www.beatport.com/search"
    _marker = "__NEXT_DATA__"

    def __init__(self) -> None:
        self._limiter = RateLimiter(0.5)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки на публичной странице поиска Beatport."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        query = f"{artist} {title}".strip() if artist else title
        params = urllib.parse.urlencode({"q": query})
        url = f"{self._search_url}?{params}"

        self._limiter.wait()
        request = urllib.request.Request(
            url, headers={"Accept": "text/html", "User-Agent": self._user_agent()}
        )
        payload = urlopen_with_retry(request, provider_label="Beatport")
        html = payload.decode("utf-8", errors="ignore")

        data = extract_embedded_json(html, self._marker)
        tracks = self._tracks(data)
        return [
            self._candidate(item)
            for item in tracks[: max(1, limit)]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _tracks(data: Any) -> list[Any]:
        try:
            queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
        except (KeyError, TypeError):
            return []
        if not isinstance(queries, list):
            return []
        for query in queries:
            if not isinstance(query, dict):
                continue
            state_data = (query.get("state") or {}).get("data")
            tracks = (state_data or {}).get("tracks") if isinstance(state_data, dict) else None
            items = tracks.get("data") if isinstance(tracks, dict) else None
            if isinstance(items, list):
                return items
        return []

    def _candidate(self, item: dict[str, Any]) -> MetadataCandidate:
        artists = item.get("artists")
        artist_names: list[str] = []
        if isinstance(artists, list):
            for entry in artists:
                if isinstance(entry, dict):
                    name = entry.get("artist_name")
                    if isinstance(name, str) and name:
                        artist_names.append(name)

        title = str(item.get("track_name") or "")
        mix_name = item.get("mix_name")
        if isinstance(mix_name, str) and mix_name and mix_name.lower() != "original mix":
            title = f"{title} ({mix_name})"

        release = item.get("release") if isinstance(item.get("release"), dict) else {}
        album = str(release.get("release_name") or "")
        artwork_url = self._artwork_url(release)

        genres = item.get("genre")
        genre = ""
        if isinstance(genres, list) and genres and isinstance(genres[0], dict):
            genre = str(genres[0].get("genre_name") or "")

        year = str(item.get("release_date") or item.get("publish_date") or "")[:4]

        bpm = item.get("bpm")
        bpm_value = float(bpm) if isinstance(bpm, (int, float)) else None

        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=str(item.get("track_id") or ""),
            title=title,
            artist=" & ".join(artist_names),
            album=album,
            year=year,
            release_id=str(release.get("release_id") or ""),
            artwork_url=artwork_url,
            genre=genre,
            bpm=bpm_value,
            musical_key=str(item.get("key_name") or ""),
        )

    @staticmethod
    def _artwork_url(release: dict[str, Any]) -> str:
        template = release.get("release_image_dynamic_uri")
        if isinstance(template, str) and template:
            return template.replace("{w}", "1400").replace("{h}", "1400")
        static = release.get("release_image_uri")
        return str(static or "")

    @staticmethod
    def _user_agent() -> str:
        return "DJMAKER metadata search (+https://github.com/DJMAKER)"
