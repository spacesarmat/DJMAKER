# Собственная Essentia Runtime для DJMAKER

DJMAKER не использует старые 32-битные Windows extractors Essentia. Вместо этого
репозиторий собирает собственный runtime из зафиксированного upstream commit Essentia.

## Зафиксированный upstream

- Репозиторий: `MTG/essentia`
- Commit: `66a890f285d0e1988155c12d17a2068e406cdd90`
- Дата upstream commit: 2026-08-27
- DJMAKER runtime: `2026.08.27-66a890f2-r5`
- Eigen: `3.4.0`, commit `3147391d946bb4b6c68edd901f2add6ac1f31f8c`

Не используется плавающая ветка `master` во время фактической компиляции. Это делает
результат воспроизводимым и не позволяет внезапному upstream-изменению сломать
дистрибутив DJMAKER.

## Архитектура

Декодирование и ресемплинг выполняет FFmpeg. В Essentia передаётся mono float32 PCM
с частотой 44100 Hz. Благодаря этому Essentia собирается в lightweight-режиме:

```text
--build-static
--lightweight=
--fft=KISS
--mode=release
--std=c++17
```

Мы не связываем Essentia runtime с FFmpeg, TagLib, libsamplerate, FFTW, Chromaprint или
TensorFlow. Eigen остаётся build-time header-only зависимостью upstream Essentia.

Собственный `djmaker-essentia` предоставляет:

```text
--version
--self-test
analyze --input FILE|- --sample-rate 44100
```

Команда `analyze` возвращает JSON с BPM, confidence, key, scale и key strength.
В дальнейшем Python-слой DJMAKER будет подавать PCM непосредственно по pipe из FFmpeg.

## Целевые платформы

Workflow собирает и проверяет:

- Windows x64 — cross-build на Ubuntu через `x86_64-w64-mingw32-g++`, затем реальный
  smoke-test на `windows-2025`;
- macOS Intel — `macos-15-intel`;
- macOS Apple Silicon — `macos-15` ARM64.

Windows ARM64 пока намеренно не публикуется: upstream Essentia не предоставляет готовой
поддержки этого toolchain, а добавлять непроверенный binary target в автоустановку нельзя.

## Почему нужен маленький upstream-патч для Windows

Upstream-опция Essentia `--cross-compile-mingw32` всё ещё жёстко выбирает
`i686-w64-mingw32-*`. Скрипт `scripts/patch_essentia_mingw64.py` меняет только три
имени toolchain на `x86_64-w64-mingw32-*` и не даёт upstream-конфигурации затереть
release CXXFLAGS. Скрипт проверяет точное число замен и падает, если контекст upstream
изменился.

Это не постоянный fork Essentia: исходник клонируется на конкретном commit и патчится
только внутри GitHub Actions job.

## Публикация

Workflow `.github/workflows/build-essentia-runtime.yml` запускается вручную и создаёт
или обновляет release:

```text
essentia-runtime-v2026.08.27-66a890f2-r5
```

Release содержит:

```text
djmaker-essentia-windows-amd64.zip
djmaker-essentia-darwin-amd64.tar.gz
djmaker-essentia-darwin-arm64.tar.gz
djmaker-essentia-source-2026.08.27-66a890f2-r5.tar.gz
SHA256SUMS.txt
```

Runtime manager DJMAKER выбирает asset по ОС/архитектуре, проверяет SHA-256, безопасно
распаковывает binary и выполняет `--self-test` до признания компонента рабочим.

## Запуск сборки из PowerShell

После отправки патча в GitHub:

```powershell
.\scripts\trigger_essentia_build.ps1
```

Нужен GitHub CLI `gh` с авторизацией на `spacesarmat/DJMAKER`.

## Лицензирование

Essentia распространяется под AGPL-3.0-only. `djmaker-essentia` линкуется с Essentia,
поэтому его исходник также помечен AGPL-3.0. Release прикладывает лицензию Essentia и
архив соответствующих исходников, включая точный upstream snapshot, bridge и build patch.

Основное приложение общается с `djmaker-essentia` как с отдельным процессом через
stdin/stdout. Перед публичной коммерческой дистрибуцией лицензионную модель DJMAKER
нужно отдельно проверить с юристом.
