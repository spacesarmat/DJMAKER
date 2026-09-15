# DJMAKER patch 0011 — cross-platform Essentia build fixes

Исправления:

- macOS Intel/ARM64 bridge теперь получает Eigen include flags через `pkg-config --cflags eigen3`.
- Windows x64 bridge использует тот же явный Eigen include path.
- В CI добавлена ранняя проверка наличия `Eigen/Core` и `unsupported/Eigen/CXX11/Tensor`.
- Для bridge задан `EIGEN_MPL2_ONLY`, согласованный с конфигурацией Essentia.
- Сохранён MinGW fix `_USE_MATH_DEFINES` из patch 0010.
- Runtime revision повышен до `2026.08.27-66a890f2-r3`, чтобы не смешивать старые r1/r2 артефакты с исправленной сборкой.

Патч включает исправления Windows из 0010 повторно, поэтому безопасен даже если 0010 не был корректно применён перед этой сборкой.
