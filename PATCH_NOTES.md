# DJMAKER patch 0029 — Track tag hover highlight

- Добавлена плавная подсветка тегов строки медиатеки при наведении мыши.
- Акцентные теги Key/BPM усиливаются до `PRIMARY` / `ON_PRIMARY`.
- Технические теги подсвечиваются через `PRIMARY_CONTAINER` / `ON_PRIMARY_CONTAINER`.
- Переход цвета анимирован за 120 мс с `EASE_OUT_CUBIC`.
- Hover обновляет только конкретный тег через `control.update()`, без полной перерисовки строки или страницы.
