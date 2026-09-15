# DJMAKER

DJMAKER — кроссплатформенная desktop-программа на Python/Flet для ведения музыкальной библиотеки.

## Реализовано в текущем этапе

- сканирование папок с музыкой;
- SQLite-медиатека;
- чтение технических параметров и тегов через Mutagen;
- точные дубликаты по SHA-256;
- редактирование и сохранение тегов;
- перенос/переименование файлов по шаблону;
- фоновые worker-задачи для тяжёлых операций;
- экран «Задачи» с прогрессом, остановкой, отменой и возобновлением долгих операций;
- архитектура онлайн-провайдеров;
- MusicBrainz как первый провайдер метаданных;
- системная, светлая и тёмная тема интерфейса с сохранением выбора;
- цветовые схемы DJMAKER Blue, Violet, Emerald, Amber, Graphite, «Чистый графен» и «Строгий»;
- автоматическая установка FFmpeg и собственного Essentia runtime;
- пакетный многопоточный BPM / Key / Camelot анализ через FFmpeg → Essentia;
- встроенные обложки MP3/FLAC/M4A с локальным content-addressed кэшем;
- компактный Waveform-анализ через FFmpeg с отображением формы волны в строке трека;
- локальный preview-player: воспроизведение по клику на обложку и seek по клику на waveform;
- оптимизированный SVG-waveform: широкие вертикальные полосы и частичное обновление прогресса без полной перерисовки медиатеки;
- точки расширения для нормализации и аудио-fingerprint.

## Требования

- Python 3.14+
- Windows 10/11 или macOS

## Установка

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
djmaker
```

macOS:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
djmaker
```

Также можно запустить:

```bash
python -m djmaker
```

## Тесты

```bash
python -m unittest discover -s tests -v
```

## Переменные окружения

- `DJMAKER_DATA_DIR` — переопределяет каталог данных приложения.
- `DJMAKER_MUSICBRAINZ_CONTACT` — контакт для User-Agent MusicBrainz, например URL проекта или email.

## Организация файлов

Шаблон по умолчанию:

```text
{artist}/{album}/{track:02d} - {title}.{ext}
```

Поддерживаемые поля: `artist`, `album`, `album_artist`, `title`, `year`, `track`, `disc`, `ext`.

Исходный файл не удаляется отдельно: при организации используется перенос. При конфликте имени создаётся безопасный суффикс `(1)`, `(2)` и т.д.

## Следующие этапы

- модуль нормализации с основной целью -11.5 LUFS;
- аудио-fingerprint для смысловых/музыкальных дубликатов;
- отдельные плагины Discogs, Beatport, Traxsource и DJ-пулов;
- загрузка/встраивание обложек;
- пакетная обработка тегов и файлов.
