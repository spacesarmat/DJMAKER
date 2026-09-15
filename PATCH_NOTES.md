# DJMAKER patch 0008

Исправляет запуск `scripts/trigger_essentia_build.ps1` в Windows PowerShell 5.1.

Изменения:

- PowerShell helper теперь полностью ASCII-only и не зависит от системной кодировки Windows.
- Убраны хрупкие backtick-переносы команд.
- Перед запуском проверяются `gh`, авторизация и наличие workflow в удалённом репозитории.
- Скрипт запоминает существующие workflow runs и после dispatch ждёт именно новый run.
- Добавлены регрессионные unit-тесты для кодировки helper-скрипта.
