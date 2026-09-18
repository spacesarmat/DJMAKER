"""Общие HTTP-хелперы для провайдеров метаданных: retry, троттлинг, извлечение JSON."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from djmaker.plugins.base import MetadataProviderError

DEFAULT_CONNECTION_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.5)
DEFAULT_TIMEOUT_SECONDS = 15.0


def urlopen_with_retry(
    request: urllib.request.Request,
    *,
    provider_label: str,
    connection_retry_delays: tuple[float, ...] = DEFAULT_CONNECTION_RETRY_DELAYS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Выполняет запрос с повтором при временных сетевых/DNS-сбоях.

    HTTP-ошибки (коды состояния) не повторяются здесь — их обрабатывает
    вызывающий код, у которого есть контекст (throttle, Retry-After, refresh
    токена и т.п.).
    """
    last_exc: OSError | None = None
    for delay in (*connection_retry_delays, None):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
    raise MetadataProviderError(f"{provider_label} недоступен: {last_exc}") from last_exc


def retry_after_seconds(exc: urllib.error.HTTPError, default: float = 2.0) -> float:
    """Читает заголовок Retry-After у HTTP-ошибки, иначе возвращает дефолт."""
    header = exc.headers.get("Retry-After") if exc.headers is not None else None
    try:
        return max(0.0, float(header)) if header else default
    except ValueError:
        return default


def is_server_error(exc: urllib.error.HTTPError) -> bool:
    """Транзиентная ошибка сервера (5xx) — стоит повторить запрос."""
    return 500 <= exc.code < 600


class RateLimiter:
    """Не чаще одного запроса за ``interval`` секунд, потокобезопасно."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._last_request = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_request = time.monotonic()


def extract_embedded_json(html: str, marker: str) -> Any:
    """Извлекает JSON, встроенный в HTML сразу после текстового маркера.

    Многие SSR-сайты (Next.js/Svelte и т.п.) кладут результаты поиска в
    JSON прямо в разметку страницы — рядом с id/классом-маркером вида
    ``id="__NEXT_DATA__"`` или ``id="serialized-server-data"``. Функция
    находит маркер, затем первый ``{`` или ``[`` после него и разбирает
    ровно один сбалансированный JSON-объект/массив, не трогая остальную
    страницу.
    """
    marker_index = html.find(marker)
    if marker_index == -1:
        raise MetadataProviderError(f"Маркер {marker!r} не найден на странице")

    start = None
    for index in range(marker_index + len(marker), len(html)):
        if html[index] in "{[":
            start = index
            break
    if start is None:
        raise MetadataProviderError(f"JSON после маркера {marker!r} не найден")

    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(html, start)
    except json.JSONDecodeError as exc:
        raise MetadataProviderError(f"Не удалось разобрать JSON после {marker!r}: {exc}") from exc
    return value


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(value: str) -> str:
    """Убирает HTML-теги, оставляя только текст (для noscript-фрагментов)."""
    return _TAG_RE.sub("", value).strip()


_STATE_PATCH_RE = re.compile(
    r"__STATE_PATCHES__\s*=\s*window\.__STATE_PATCHES__\s*\|\|\s*\[\]\)\.push\((\[.*?\])\);?",
    re.S,
)


def collect_react_state_patches(html: str, path_pattern: "re.Pattern[str]") -> dict[int, Any]:
    """Собирает элементы из потоковых React state-patches (``window.__STATE_PATCHES__``).

    Некоторые SSR-сайты на React Server Components не кладут результаты в
    один JSON-блок, а стримят их несколькими ``<script>``-тегами как JSON
    Patch (RFC 6902) операции ``add``/``replace`` по путям вида
    ``/search/.../<index>``. Возвращает ``{индекс: value}`` для операций,
    чей ``path`` целиком совпадает с ``path_pattern`` (должен содержать
    ровно одну группу — числовой индекс).
    """
    items: dict[int, Any] = {}
    for block in _STATE_PATCH_RE.findall(html):
        try:
            patches = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(patches, list):
            continue
        for patch in patches:
            if not isinstance(patch, dict) or patch.get("op") not in ("add", "replace"):
                continue
            path = patch.get("path")
            if not isinstance(path, str):
                continue
            match = path_pattern.fullmatch(path)
            if match:
                items[int(match.group(1))] = patch.get("value")
    return items
