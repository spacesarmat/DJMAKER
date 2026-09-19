"""Цветовой бакет энергии (AIR) трека: энергия + лад + шумность + жанровый тег.

Чистая функция без зависимостей от UI/сервисов — UI-слой мапит bucket_id на
конкретный ft.Colors и текстовую метку. Жанровый тег (см.
``djmaker.services.genre_inference``) опционален: при его отсутствии решение
принимается по DSP-эвристике (energy/лад/noisiness).
"""

from __future__ import annotations

# Теги AST/AudioSet (см. GenreClassifier.classify) — единый словарь между
# доменным слоем и сервисом инференса.
GENRE_TAG_HEAVY_DARK = "heavy_dark"
GENRE_TAG_AMBIENT = "ambient"

BUCKET_RED = "red"
BUCKET_YELLOW = "yellow"
BUCKET_ORANGE = "orange"
BUCKET_GREEN = "green"
BUCKET_BLUE = "blue"
BUCKET_PURPLE = "purple"
BUCKET_BLACK = "black"
BUCKET_UNKNOWN = ""

# Пороги эмпирические, подобраны по типичной поп/электронной музыке;
# вынесены константами, чтобы их можно было отдельно откалибровать.
_HIGH_ENERGY = 80.0
_MEDIUM_HIGH_ENERGY = 55.0
_MEDIUM_ENERGY = 30.0
_LOW_ENERGY = 10.0
_HIGH_NOISINESS = 0.4


def energy_color_bucket(
    energy: float | None,
    scale: str,
    noisiness: float | None,
    genre_tag: str = "",
) -> str:
    """Возвращает семантический bucket_id цвета для подсветки строки трека.

    Приоритет: уверенный жанровый тег AST -> DSP-эвристика по energy/ладу/
    шумности -> "" (нет данных для подсветки).
    """
    if genre_tag == GENRE_TAG_HEAVY_DARK:
        return BUCKET_BLACK
    if genre_tag == GENRE_TAG_AMBIENT:
        return BUCKET_PURPLE

    if energy is None:
        return BUCKET_UNKNOWN

    minor = scale.strip().lower() == "minor"
    noise = noisiness if noisiness is not None else 0.0

    if energy < _LOW_ENERGY:
        return BUCKET_PURPLE
    if energy < _MEDIUM_ENERGY:
        return BUCKET_BLUE
    if energy < _MEDIUM_HIGH_ENERGY:
        return BUCKET_GREEN
    if energy < _HIGH_ENERGY:
        return BUCKET_ORANGE
    if minor or noise > _HIGH_NOISINESS:
        return BUCKET_RED
    return BUCKET_YELLOW
