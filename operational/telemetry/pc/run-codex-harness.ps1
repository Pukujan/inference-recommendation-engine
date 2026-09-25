[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [string]$WorkingDirectory = (Get-Location).Path,
    [string]$TaskId = 'codex-harness',
    [ValidateSet('main', 'subagent')]
    [string]$Topology = 'main',
    [string]$ReceiptRoot = '',
    [string]$EnvFile = 'D:\claude\inferhub\.env',
    [string]$TelemetryScript = '',
    [string]$ModelRoute = '',
    [string]$LlmBaseUrlHost = 'api.inferhub.dev'
)

# InferHub-key Codex launcher with the Astra OTel wrapper (llm.provider=inferhub).
# Part of IRE #41. Non-InferHub keys must use repo-root run_codex_harness.ps1 without this script.
$ErrorActionPreference = 'Stop'

function Resolve-TelemetryScript {
    param([string]$Override)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override)) { throw "Telemetry script not found: $Override" }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $candidates = @(
        (Join-Path $PSScriptRoot 'astra-telemetry.ps1'),
        'D:\claude\_workspace\telemetry\astra-telemetry.ps1'
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path }
    }
    throw 'astra-telemetry.ps1 not found next to this script or under D:\claude\_workspace\telemetry\'
}

function Read-EnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "Env file not found: $Path" }
    $map = @{}
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        if ($rawLine -match '^\s*(INFERHUB_API_KEY|INFERHUB_API_URL)\s*=\s*(.*?)\s*$') {
            $value = $Matches[2]
            if ($value.Length -ge 2 -and (
                    ($value[0] -eq '"' -and $value[-1] -eq '"') -or
                    ($value[0] -eq "'" -and $value[-1] -eq "'")
                )) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $map[$Matches[1]] = $value
        }
    }
    foreach ($requiredName in @('INFERHUB_API_KEY', 'INFERHUB_API_URL')) {
        if (-not $map.ContainsKey($requiredName) -or [string]::IsNullOrWhiteSpace($map[$requiredName])) {
            throw "Env file must define $requiredName."
        }
    }
    return $map
}

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw 'codex CLI not found on PATH.'
}
$gitCommand = Get-Command git -CommandType Application -ErrorAction Stop
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).Path
$repositoryRoot = & $gitCommand.Source -C $resolvedWorkingDirectory rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repositoryRoot)) {
    throw "Working directory is not inside a Git repository: $resolvedWorkingDirectory"
}
$repositoryRoot = (Resolve-Path -LiteralPath $repositoryRoot.Trim() -ErrorAction Stop).Path

$envMap = Read-EnvFile -Path $EnvFile
$env:INFERHUB_API_KEY = $envMap['INFERHUB_API_KEY']
$env:INFERHUB_API_URL = $envMap['INFERHUB_API_URL']

if (-not $ReceiptRoot) {
    $ReceiptRoot = Join-Path $PSScriptRoot '..\..\..\.ire\codex-harness-receipts'
    $ReceiptRoot = [System.IO.Path]::GetFullPath($ReceiptRoot)
}
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$receiptDir = Join-Path $ReceiptRoot $stamp
New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
$eventsPath = Join-Path $receiptDir 'codex-events.jsonl'
$stderrPath = Join-Path $receiptDir 'codex-stderr.txt'
$finalPath = Join-Path $receiptDir 'final-message.md'
$effectiveModelRoute = if ($ModelRoute) { $ModelRoute } else { $Model }

. (Resolve-TelemetryScript -Override $TelemetryScript)
$otel = Start-AstraTelemetry -Action 'harness' -TaskId $TaskId -Topology $Topology -Harness 'codex' -CorrelationId "codex-harness-$stamp" -ReceiptDir $receiptDir -ModelRoute $effectiveModelRoute -LlmProvider 'inferhub' -LlmBaseUrlHost $LlmBaseUrlHost
$otelCodexArgs = Get-AstraCodexOtelArgs

$codexArguments = @(
    'exec',
    '--sandbox', 'danger-full-access',
    '--config', 'approval_policy=never',
    '--config', 'model_provider="inferhub"',
    '--config', 'model_providers.inferhub.name="InferHub"',
    '--config', ('model_providers.inferhub.base_url="{0}"' -f $envMap['INFERHUB_API_URL'].TrimEnd('/')),
    '--config', 'model_providers.inferhub.env_key="INFERHUB_API_KEY"',
    '--config', 'model_providers.inferhub.wire_api="responses"',
    '--config', 'shell_environment_policy.inherit=all'
) + $otelCodexArgs + @(
    '--json',
    '--cd', $repositoryRoot,
    '--model', $Model,
    '--output-last-message', $finalPath,
    '-'
)

$codexExitCode = 1
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $Prompt | & codex @codexArguments 2>> $stderrPath | Tee-Object -FilePath $eventsPath
    if ($null -ne $LASTEXITCODE) { $codexExitCode = [int]$LASTEXITCODE }
    $ErrorActionPreference = $previousErrorAction
} catch {
    try { Add-Content -LiteralPath $stderrPath -Value ("harness-catch: " + $_.Exception.Message) } catch {}
    $codexExitCode = 1
    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $codexExitCode = [int]$LASTEXITCODE }
} finally {
    Stop-AstraTelemetry -State $otel -ExitCode $codexExitCode -StderrPath $stderrPath -EventsPath $eventsPath
    Remove-Item Env:INFERHUB_API_KEY -ErrorAction SilentlyContinue
}
if ((Test-Path -LiteralPath $stderrPath) -and ((Get-Item -LiteralPath $stderrPath).Length -gt 0) -and ($codexExitCode -ne 0)) {
    Write-Output ("stderr_tail=" + ((Get-Content -LiteralPath $stderrPath -Tail 20) -join ' | '))
}

$summary = @(
    "exit_code=$codexExitCode"
    "model=$Model"
    "provider=inferhub"
    "sandbox=danger-full-access"
    "approval_policy=never"
    "working_directory=$repositoryRoot"
    "events=$eventsPath"
    "trace_id=$($otel.trace_id)"
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $receiptDir 'summary.txt'), $summary + [Environment]::NewLine)
Write-Output "receipt_dir=$receiptDir"
Write-Output "trace_id=$($otel.trace_id)"

exit $codexExitCode
