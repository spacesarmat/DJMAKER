# DJMAKER patch 0012 — pinned Eigen 3.4.0 for Essentia runtime

Исправления:

- Убрана зависимость Essentia build от системного/Homebrew Eigen.
- Для Windows x64, macOS Intel и macOS Apple Silicon закреплён Eigen 3.4.0 commit `3147391d946bb4b6c68edd901f2add6ac1f31f8c`.
- Добавлен собственный `eigen3.pc`, поэтому waf получает одинаковые заголовки Eigen на всех runner-ах.
- Сохранён Windows MinGW фикс `_USE_MATH_DEFINES`.
- `actions/checkout` обновлён до v7, `upload-artifact` до v7, `download-artifact` до v8 (Node 24).
- Runtime revision повышен до `2026.08.27-66a890f2-r4`.
- Runtime archive теперь содержит лицензию Eigen MPL2.
- Source release содержит точные исходники и Essentia, и Eigen, использованные для сборки.
