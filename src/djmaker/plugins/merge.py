"""Объединение кандидатов метаданных из нескольких провайдеров."""

from __future__ import annotations

import re

from djmaker.domain.models import MetadataCandidate

_MERGEABLE_FIELDS = ("title", "artist", "album", "year", "artwork_url", "genre", "release_id")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip().lower())


def merge_candidates(per_provider: list[list[MetadataCandidate]]) -> list[MetadataCandidate]:
    """Группирует кандидатов по (title, artist) и объединяет непустые поля.

    Порядок списков в ``per_provider`` задаёт приоритет: при конфликте в поле
    побеждает первый непустой источник. Кандидаты без совпадения в других
    провайдерах остаются в выдаче как есть.
    """
    groups: dict[tuple[str, str], list[MetadataCandidate]] = {}
    order: list[tuple[str, str]] = []

    for candidates in per_provider:
        for candidate in candidates:
            key = (_normalize(candidate.title), _normalize(candidate.artist))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(candidate)

    merged: list[MetadataCandidate] = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            merged.append(group[0])
            continue

        fields: dict[str, str] = {}
        for field_name in _MERGEABLE_FIELDS:
            for candidate in group:
                value = getattr(candidate, field_name)
                if value:
                    fields[field_name] = value
                    break

        provider_ids = []
        for candidate in group:
            if candidate.provider_id not in provider_ids:
                provider_ids.append(candidate.provider_id)

        merged.append(
            MetadataCandidate(
                provider_id="+".join(provider_ids),
                external_id=group[0].external_id,
                title=fields.get("title", ""),
                artist=fields.get("artist", ""),
                album=fields.get("album", ""),
                year=fields.get("year", ""),
                release_id=fields.get("release_id", ""),
                artwork_url=fields.get("artwork_url", ""),
                genre=fields.get("genre", ""),
            )
        )

    return merged
