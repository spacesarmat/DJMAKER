"""Контракты внешних источников метаданных."""

from __future__ import annotations

from abc import ABC, abstractmethod

from djmaker.domain.models import MetadataCandidate, TrackRecord


class MetadataProviderError(RuntimeError):
    """Ошибка внешнего провайдера метаданных."""


class MetadataProvider(ABC):
    """Интерфейс отдельной онлайн-библиотеки или DJ-пула."""

    provider_id: str
    display_name: str

    @abstractmethod
    def search(self, track: TrackRecord, limit: int = 10) -> list[MetadataCandidate]:
        """Ищет подходящие метаданные для локального трека."""
        raise NotImplementedError
