# DJMAKER patch 0007 — собственная современная Essentia

- Удалена зависимость от старых Windows Essentia extractors 2.1 beta5/i686.
- Добавлен воспроизводимый GitHub Actions build из pinned upstream Essentia commit.
- Windows runtime теперь x64 MinGW и проходит smoke-test на настоящем Windows runner.
- macOS собирается отдельно для Intel и Apple Silicon.
- Добавлен собственный `djmaker-essentia` bridge: BPM + Key, JSON output, self-test.
- FFmpeg декодирует аудио; Essentia собирается lightweight + KISS FFT.
- Runtime manager скачивает наши Releases и проверяет SHA-256.
- Release workflow публикует corresponding source archive для AGPL-сборки.
- Добавлен PowerShell helper для запуска/наблюдения GitHub Actions build.
