"""Плагин SoundCloud: публичная страница поиска (без входа, без API-ключа).

SoundCloud подгружает основной список результатов через приватный
client_id из своего JS-бандла — DJMAKER его не извлекает (это обход их
закрытой регистрации API). Вместо этого разбирается честный SEO/accessibility
<noscript>-список, который сервер отдаёт без выполнения JS: он даёт
title/ссылку, но не альбом/год/BPM.
"""

from __future__ import annotations

import html as html_module
import re
import urllib.parse
import urllib.request

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import RateLimiter, urlopen_with_retry

_NOSCRIPT_RE = re.compile(r"<noscript>(.*?)</noscript>", re.S)
_RESULT_RE = re.compile(
    r'<li><h2><a href="(?P<href>/[^"]+)">(?P<title>[^<]+)</a></h2></li>'
)


class SoundCloudProvider(MetadataProvider):
    """Провайдер честного noscript-списка результатов поиска SoundCloud."""

    provider_id = "soundcloud"
    display_name = "SoundCloud"
    _search_url = "https://soundcloud.com/search"

    def __init__(self) -> None:
        self._limiter = RateLimiter(0.5)

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки в noscript-списке результатов поиска SoundCloud."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        query = f"{artist} {title}".strip() if artist else title
        params = urllib.parse.urlencode({"q": query})
        url = f"{self._search_url}?{params}"

        self._limiter.wait()
        request = urllib.request.Request(
            url, headers={"Accept": "text/html", "User-Agent": self._user_agent()}
        )
        payload = urlopen_with_retry(request, provider_label="SoundCloud")
        html_text = payload.decode("utf-8", errors="ignore")

        noscript_blocks = _NOSCRIPT_RE.findall(html_text)
        if not noscript_blocks:
            raise MetadataProviderError("SoundCloud: не удалось разобрать страницу поиска")

        # Результаты — только <li><h2><a>...</a></h2></li>; статичное меню
        # категорий поиска ("Search for Tracks" и т.п.) использует <li><a>
        # без <h2> и этим регулярным выражением не захватывается.
        combined = "\n".join(noscript_blocks)
        candidates = []
        for match in _RESULT_RE.finditer(combined):
            if len(candidates) >= max(1, limit):
                break
            candidates.append(self._candidate(match.group("href"), match.group("title")))
        return candidates

    def _candidate(self, href: str, raw_title: str) -> MetadataCandidate:
        title = html_module.unescape(raw_title).strip()
        artist = ""
        # Ссылки на треки — "/пользователь/название-трека"; на профиль/сет — короче.
        slug = href.strip("/").split("/")
        if len(slug) >= 1:
            artist = slug[0].replace("-", " ").strip()
        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=href,
            title=title,
            artist=artist,
        )

    @staticmethod
    def _user_agent() -> str:
        return "DJMAKER metadata search (+https://github.com/DJMAKER)"
