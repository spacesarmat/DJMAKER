[CmdletBinding()]
param(
    [string]$Repository = "spacesarmat/DJMAKER",
    [switch]$NoWatch
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) не найден. Установите: winget install --id GitHub.cli"
}

& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI не авторизован. Выполните: gh auth login"
}

Write-Host "Запускаю Build Essentia Runtime для $Repository..."
& gh workflow run build-essentia-runtime.yml --repo $Repository
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось запустить workflow"
}

if ($NoWatch) {
    Write-Host "Workflow запущен. Проверяйте Actions в GitHub."
    exit 0
}

Start-Sleep -Seconds 5
$raw = & gh run list `
    --repo $Repository `
    --workflow build-essentia-runtime.yml `
    --limit 1 `
    --json databaseId,status,conclusion,url
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить workflow run"
}

$run = $raw | ConvertFrom-Json
if (-not $run -or -not $run[0].databaseId) {
    throw "GitHub не вернул идентификатор запуска"
}

$runId = $run[0].databaseId
Write-Host "Workflow run: $($run[0].url)"
& gh run watch $runId --repo $Repository --exit-status
if ($LASTEXITCODE -ne 0) {
    throw "Сборка Essentia завершилась ошибкой"
}

Write-Host "Essentia runtime собран и опубликован в Releases."
