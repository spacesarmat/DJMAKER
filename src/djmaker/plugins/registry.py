"""Реестр внешних провайдеров DJMAKER."""

from __future__ import annotations

from djmaker.plugins.base import MetadataProvider
from djmaker.plugins.providers.musicbrainz import MusicBrainzProvider
from djmaker.plugins.providers.spotify import SpotifyProvider


class PluginRegistry:
    """Хранит доступные плагины и обеспечивает поиск по provider_id."""

    def __init__(self) -> None:
        self._providers: dict[str, MetadataProvider] = {}
        self.register(MusicBrainzProvider())
        self.register(SpotifyProvider())

    def register(self, provider: MetadataProvider) -> None:
        """Регистрирует провайдер, не позволяя случайно заменить существующий."""
        if provider.provider_id in self._providers:
            raise ValueError(f"Провайдер уже зарегистрирован: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> MetadataProvider:
        """Возвращает провайдер по идентификатору."""
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"Неизвестный провайдер: {provider_id}") from exc

    def all(self) -> list[MetadataProvider]:
        """Возвращает зарегистрированные плагины в стабильном порядке."""
        return sorted(self._providers.values(), key=lambda provider: provider.display_name.lower())
