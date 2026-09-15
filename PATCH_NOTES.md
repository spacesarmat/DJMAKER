# Patch 0015 — close SQLite migration fixture connection

- Исправлен Windows-only сбой очистки `TemporaryDirectory` в тесте миграции БД.
- `sqlite3.Connection` теперь явно закрывается через `contextlib.closing`.
- Производственный код SQLite не менялся: дефект находился только в тестовой фикстуре.
