# DJMAKER patch 0009

Исправлена совместимость `scripts/trigger_essentia_build.ps1` с Windows PowerShell 5.1.

Причина: `ConvertFrom-Json` в Windows PowerShell 5.1 может возвращать JSON-массив от `gh run list` как единый массив-объект. При `Set-StrictMode` обращение к свойству `databaseId` такого контейнера вызывает `PropertyNotFoundStrict`.

Изменения:

- JSON больше не разбирается средствами PowerShell.
- GitHub CLI сам извлекает `databaseId` через `--jq '.[].databaseId'`.
- ID workflow runs обрабатываются как обычные строки.
- Сохранена защита от захвата старого workflow run.
- Helper остаётся полностью ASCII-only для Windows PowerShell 5.1.
- Добавлен регрессионный unit-тест против возврата к `ConvertFrom-Json`.
