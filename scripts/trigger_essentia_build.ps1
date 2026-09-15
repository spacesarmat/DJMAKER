[CmdletBinding()]
param(
    [string]$Repository = "spacesarmat/DJMAKER",
    [switch]$NoWatch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Workflow = "build-essentia-runtime.yml"

function Assert-GhSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) was not found. Install it with: winget install --id GitHub.cli"
}

Write-Host "Checking GitHub authentication..."
& gh auth status --hostname github.com
Assert-GhSuccess "GitHub CLI is not authenticated. Run: gh auth login"

Write-Host "Checking remote workflow: $Workflow"
& gh workflow view $Workflow --repo $Repository *> $null
Assert-GhSuccess "Workflow $Workflow is not available in $Repository. Commit and push the workflow first."

$beforeRaw = & gh run list --repo $Repository --workflow $Workflow --event workflow_dispatch --limit 20 --json databaseId
Assert-GhSuccess "Could not list existing workflow runs."

$beforeRuns = @()
if ($beforeRaw) {
    $beforeRuns = @($beforeRaw | ConvertFrom-Json)
}
$beforeIds = @($beforeRuns | ForEach-Object { [string]$_.databaseId })

Write-Host "Starting Essentia runtime build for $Repository..."
& gh workflow run $Workflow --repo $Repository
Assert-GhSuccess "Could not start the Essentia runtime workflow."

if ($NoWatch) {
    Write-Host "Workflow was started. Check GitHub Actions for progress."
    exit 0
}

Write-Host "Waiting for the new workflow run to appear..."
$run = $null
$deadline = (Get-Date).AddSeconds(90)

while ($null -eq $run -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3

    $raw = & gh run list --repo $Repository --workflow $Workflow --event workflow_dispatch --limit 20 --json databaseId,status,conclusion,url,createdAt
    Assert-GhSuccess "Could not query workflow runs after dispatch."

    $runs = @()
    if ($raw) {
        $runs = @($raw | ConvertFrom-Json)
    }

    foreach ($candidate in $runs) {
        $candidateId = [string]$candidate.databaseId
        if ($beforeIds -notcontains $candidateId) {
            $run = $candidate
            break
        }
    }
}

if ($null -eq $run -or -not $run.databaseId) {
    throw "GitHub did not return a new workflow run within 90 seconds. Open Actions and check the workflow manually."
}

$runId = [string]$run.databaseId
Write-Host "Workflow run: $($run.url)"
Write-Host "Watching run ID: $runId"

& gh run watch $runId --repo $Repository --exit-status
Assert-GhSuccess "Essentia runtime build failed. Open the workflow run URL above for details."

Write-Host "Essentia runtime build completed successfully and was published to Releases."
