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

function Get-WorkflowRunIds {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryName,
        [Parameter(Mandatory = $true)]
        [string]$WorkflowName
    )

    $rawIds = @(& gh run list --repo $RepositoryName --workflow $WorkflowName --event workflow_dispatch --limit 20 --json databaseId --jq '.[].databaseId')
    Assert-GhSuccess "Could not list workflow runs."

    return @(
        $rawIds |
            Where-Object { $null -ne $_ -and ([string]$_).Trim().Length -gt 0 } |
            ForEach-Object { ([string]$_).Trim() }
    )
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

$beforeIds = @(Get-WorkflowRunIds -RepositoryName $Repository -WorkflowName $Workflow)

Write-Host "Starting Essentia runtime build for $Repository..."
& gh workflow run $Workflow --repo $Repository
Assert-GhSuccess "Could not start the Essentia runtime workflow."

if ($NoWatch) {
    Write-Host "Workflow was started. Check GitHub Actions for progress."
    exit 0
}

Write-Host "Waiting for the new workflow run to appear..."
$runId = $null
$deadline = (Get-Date).AddSeconds(90)

while ($null -eq $runId -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3

    $currentIds = @(Get-WorkflowRunIds -RepositoryName $Repository -WorkflowName $Workflow)
    foreach ($candidateId in $currentIds) {
        if ($beforeIds -notcontains $candidateId) {
            $runId = $candidateId
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace([string]$runId)) {
    throw "GitHub did not return a new workflow run within 90 seconds. Open Actions and check the workflow manually."
}

$runUrl = "https://github.com/$Repository/actions/runs/$runId"
Write-Host "Workflow run: $runUrl"
Write-Host "Watching run ID: $runId"

& gh run watch $runId --repo $Repository --exit-status
Assert-GhSuccess "Essentia runtime build failed. Open the workflow run URL above for details."

Write-Host "Essentia runtime build completed successfully and was published to Releases."
