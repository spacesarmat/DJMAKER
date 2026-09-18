"""Плагин Spotify: поиск метаданных и обложек через Web API."""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from djmaker.domain.models import MetadataCandidate, TrackRecord
from djmaker.plugins.base import MetadataProvider, MetadataProviderError
from djmaker.plugins.http_utils import retry_after_seconds, urlopen_with_retry


class SpotifyProvider(MetadataProvider):
    """Провайдер Spotify Web API (Client Credentials, без входа пользователя)."""

    provider_id = "spotify"
    display_name = "Spotify"
    _token_url = "https://accounts.spotify.com/api/token"
    _search_url = "https://api.spotify.com/v1/search"
    _artists_url = "https://api.spotify.com/v1/artists"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._client_id: str | None = None
        self._client_secret: str | None = None

    def configure(self, client_id: str, client_secret: str) -> None:
        """Задаёт credentials из настроек приложения (приоритетнее переменных окружения)."""
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        with self._lock:
            if client_id != (self._client_id or "") or client_secret != (self._client_secret or ""):
                self._token = None
                self._token_expires_at = 0.0
            self._client_id = client_id or None
            self._client_secret = client_secret or None

    def is_configured(self) -> bool:
        with self._lock:
            has_explicit = bool(self._client_id and self._client_secret)
        return has_explicit or bool(
            os.getenv("DJMAKER_SPOTIFY_CLIENT_ID") and os.getenv("DJMAKER_SPOTIFY_CLIENT_SECRET")
        )

    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет треки Spotify по title/artist и дополняет их жанрами авторов."""
        title = track.metadata.title or track.path.stem
        artist = track.metadata.artist
        query = f"track:{title}"
        if artist:
            query += f" artist:{artist}"
        params = urllib.parse.urlencode({"q": query, "type": "track", "limit": max(1, min(limit, 50))})
        url = f"{self._search_url}?{params}"

        data = self._request_json(url)
        items = ((data.get("tracks") or {}).get("items")) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise MetadataProviderError("Spotify вернул неожиданный формат данных")

        genres_by_artist = self._artist_genres(items)
        return [self._candidate(item, genres_by_artist) for item in items if isinstance(item, dict)]

    def _candidate(
        self, item: dict[str, Any], genres_by_artist: dict[str, list[str]]
    ) -> MetadataCandidate:
        artists = item.get("artists") or []
        artist_names: list[str] = []
        artist_ids: list[str] = []
        if isinstance(artists, list):
            for entry in artists:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    if isinstance(name, str) and name:
                        artist_names.append(name)
                    artist_id = entry.get("id")
                    if isinstance(artist_id, str) and artist_id:
                        artist_ids.append(artist_id)

        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        album_name = str(album.get("name") or "")
        year = str(album.get("release_date") or "")[:4]
        artwork_url = self._largest_image(album.get("images"))

        genre = ""
        for artist_id in artist_ids:
            found = genres_by_artist.get(artist_id) or []
            if found:
                genre = found[0]
                break

        return MetadataCandidate(
            provider_id=self.provider_id,
            external_id=str(item.get("id") or ""),
            title=str(item.get("name") or ""),
            artist=" & ".join(artist_names),
            album=album_name,
            year=year,
            release_id=str(album.get("id") or ""),
            artwork_url=artwork_url,
            genre=genre,
        )

    def _artist_genres(self, items: list[Any]) -> dict[str, list[str]]:
        artist_ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for entry in item.get("artists") or []:
                if isinstance(entry, dict):
                    artist_id = entry.get("id")
                    if isinstance(artist_id, str) and artist_id and artist_id not in artist_ids:
                        artist_ids.append(artist_id)
        if not artist_ids:
            return {}

        genres_by_artist: dict[str, list[str]] = {}
        for batch_start in range(0, len(artist_ids), 50):
            batch = artist_ids[batch_start : batch_start + 50]
            params = urllib.parse.urlencode({"ids": ",".join(batch)})
            data = self._request_json(f"{self._artists_url}?{params}")
            artists = data.get("artists") if isinstance(data, dict) else None
            if not isinstance(artists, list):
                continue
            for artist in artists:
                if not isinstance(artist, dict):
                    continue
                artist_id = artist.get("id")
                genres = artist.get("genres")
                if isinstance(artist_id, str) and isinstance(genres, list):
                    genres_by_artist[artist_id] = [g for g in genres if isinstance(g, str) and g]
        return genres_by_artist

    @staticmethod
    def _largest_image(images: Any) -> str:
        if not isinstance(images, list):
            return ""
        best_url = ""
        best_width = -1
        for image in images:
            if not isinstance(image, dict):
                continue
            url = image.get("url")
            width = image.get("width") or 0
            if isinstance(url, str) and url and int(width) > best_width:
                best_url = url
                best_width = int(width)
        return best_url

    def _request_json(self, url: str, *, retried: bool = False) -> Any:
        token = self._access_token()
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"}
        )
        try:
            payload = urlopen_with_retry(request, provider_label="Spotify")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and not retried:
                time.sleep(retry_after_seconds(exc))
                return self._request_json(url, retried=True)
            if exc.code == 401 and not retried:
                with self._lock:
                    self._token = None
                return self._request_json(url, retried=True)
            raise MetadataProviderError(f"Spotify недоступен: {exc}") from exc
        except MetadataProviderError as exc:
            raise MetadataProviderError(self._connection_error_message(exc.__cause__)) from exc

        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetadataProviderError(f"Некорректный ответ Spotify: {exc}") from exc

    @staticmethod
    def _connection_error_message(exc: BaseException | None) -> str:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror):
            return (
                "Spotify недоступен: не удалось разрешить DNS-имя accounts.spotify.com / "
                "api.spotify.com. Проверьте интернет-соединение, VPN или прокси "
                f"(исходная ошибка: {exc})."
            )
        return f"Spotify недоступен: {exc}"

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            client_id = self._client_id or os.getenv("DJMAKER_SPOTIFY_CLIENT_ID")
            client_secret = self._client_secret or os.getenv("DJMAKER_SPOTIFY_CLIENT_SECRET")
            if not client_id or not client_secret:
                raise MetadataProviderError(
                    "Spotify не настроен: задайте DJMAKER_SPOTIFY_CLIENT_ID и "
                    "DJMAKER_SPOTIFY_CLIENT_SECRET"
                )

            credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            request = urllib.request.Request(
                self._token_url,
                data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                payload = urlopen_with_retry(request, provider_label="Spotify")
            except urllib.error.HTTPError as exc:
                raise MetadataProviderError(
                    f"Spotify: не удалось получить токен (проверьте Client ID/Secret): {exc}"
                ) from exc
            except MetadataProviderError as exc:
                raise MetadataProviderError(self._connection_error_message(exc.__cause__)) from exc

            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MetadataProviderError(f"Spotify: некорректный ответ токена: {exc}") from exc

            token = data.get("access_token") if isinstance(data, dict) else None
            expires_in = data.get("expires_in") if isinstance(data, dict) else None
            if not isinstance(token, str) or not token:
                raise MetadataProviderError("Spotify: токен не получен")

            self._token = token
            try:
                ttl = float(expires_in)
            except (TypeError, ValueError):
                ttl = 3600.0
            self._token_expires_at = time.monotonic() + max(0.0, ttl - 30.0)
            return self._token
