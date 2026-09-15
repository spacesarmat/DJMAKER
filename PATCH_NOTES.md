# DJMAKER patch 0019 — Windows-safe artwork cache writes

Исправлена гонка при одновременном сохранении одной и той же встроенной обложки.

На Windows несколько потоков могли одновременно вызвать `Path.replace()` для
одного content-addressed target-файла и получить `WinError 5`.

Изменения:
- `ArtworkCache` сериализует критическую секцию записи через `threading.Lock`;
- проверка существующего target выполняется внутри блокировки;
- только первый поток пишет temporary-файл и делает atomic replace;
- остальные потоки получают уже существующий target;
- добавлен платформонезависимый regression-тест, эмулирующий Windows replace race.
