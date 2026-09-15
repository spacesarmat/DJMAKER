# Runtime-зависимости DJMAKER

DJMAKER не хранит крупные аудио-бинарники в Git-репозитории. При старте приложение
проверяет FFmpeg и Essentia и при необходимости устанавливает управляемые копии в
локальный каталог данных приложения.

## FFmpeg

- Windows x64/ARM64 и macOS Intel/Apple Silicon.
- Сначала используется управляемая копия DJMAKER, затем системный `PATH`.
- Автоматическая загрузка выполняется из release-архивов `binmgr/ffmpeg`.
- Перед распаковкой архив проверяется по `SHA256SUMS.txt` того же GitHub Release.
- Устанавливаются `ffmpeg` и `ffprobe`.

FFmpeg отвечает за чтение музыкальных контейнеров, декодирование и подготовку mono
float32 PCM 44100 Hz для DSP-модулей.

## Essentia

DJMAKER использует собственную современную lightweight-сборку Essentia, а не старые
официальные Windows extractors.

Поддерживаемые runtime assets:

- Windows x64;
- macOS Intel;
- macOS Apple Silicon.

Сборка создаётся workflow `Build Essentia Runtime` из точного upstream commit
`66a890f285d0e1988155c12d17a2068e406cdd90`. Она использует KISS FFT и не включает
внутрь Essentia декодеры FFmpeg. Это уменьшает бинарник и устраняет старые Windows
3rd-party зависимости upstream.

Runtime manager скачивает asset из Releases `spacesarmat/DJMAKER`, проверяет
`SHA256SUMS.txt`, безопасно распаковывает и запускает встроенный `--self-test`.

Python-пакет `essentia`, установленный пользователем отдельно, остаётся только резервным
backend и не является основным способом работы DJMAKER.

Подробности сборки: `docs/ESSENTIA_RUNTIME_BUILD.md`.

## Каталог установки

Windows:

`%LOCALAPPDATA%\DJMAKER\runtime`

macOS:

`~/Library/Application Support/DJMAKER/runtime/`

Удаление каталога `runtime` безопасно: при следующем запуске DJMAKER проверит и снова
установит недостающие компоненты.
