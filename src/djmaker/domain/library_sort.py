"""Ключи сортировки и нормализация значений музыкальной библиотеки."""

from __future__ import annotations

import math
import re

LIBRARY_SORT_LABELS = {
    "artist": "Исполнитель / название",
    "title": "Название",
    "bpm": "BPM",
    "energy": "Энергия",
    "camelot": "Тональность Camelot",
    "added": "Дата добавления",
    "year": "Год выпуска",
    "duration": "Длительность",
}
DEFAULT_LIBRARY_SORT = "artist"


def positive_number(value: object) -> float | None:
    """Неизвестные, нулевые и некорректные значения не участвуют в порядке."""
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def release_year(value: object) -> int | None:
    """Принимает год или дату тега, например 2024-06-01."""
    match = re.match(r"^([1-9][0-9]{3})(?:$|[-/. ])", str(value).strip())
    return int(match[1]) if match else None


def camelot_order(value: object) -> int | None:
    """Порядок 1A, 1B, 2A, 2B…12B; понимает также Am, C major, F# minor."""
    text = str(value or "").strip().replace("♯", "#").replace("♭", "b")
    code = re.fullmatch(r"(1[0-2]|[1-9])([AB])", text.upper())
    if code:
        return (int(code[1]) - 1) * 2 + (code[2] == "B")
    note = re.fullmatch(
        r"([A-G])([#b]?)(?:\s*(major|minor|maj|min|m))?", text, re.IGNORECASE
    )
    if not note:
        return None
    pitches = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pitch = pitches[note[1].upper()]
    pitch = (pitch + {"#": 1, "b": -1, "": 0}[note[2].lower()]) % 12
    scale = note[3] or "major"
    minor = scale != "M" and scale.lower() in {"minor", "min", "m"}
    number = (pitch * 7 + (4 if minor else 7)) % 12 + 1
    return (number - 1) * 2 + (not minor)
