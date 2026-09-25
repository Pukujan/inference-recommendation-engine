[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [string]$WorkingDirectory = (Get-Location).Path,

    # When set, load InferHub credentials, pin the InferHub provider, and emit the Astra OTel
    # wrapper tags (llm.provider=inferhub). Non-InferHub runs stay untagged so the collector drops them.
    [switch]$InferHub,

    [string]$TaskId = 'codex-harness',
    [ValidateSet('main', 'subagent')]
    [string]$Topology = 'main',
    [string]$ReceiptRoot = '',
    [string]$InferHubEnvFile = 'D:\claude\inferhub\.env',
    [string]$TelemetryScript = '',
    [string]$ModelRoute = '',
    [string]$LlmBaseUrlHost = 'api.inferhub.dev'
)

$ErrorActionPreference = 'Stop'

function Resolve-CodexHarnessTelemetryScript {
    param([string]$Override)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override)) {
            throw "Telemetry script not found: $Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $candidates = @(
        (Join-Path $PSScriptRoot 'operational\telemetry\pc\astra-telemetry.ps1'),
        'D:\claude\_workspace\telemetry\astra-telemetry.ps1'
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            return (Resolve-Path -LiteralPath $c).Path
        }
    }
    throw 'astra-telemetry.ps1 not found under operational/telemetry/pc/ or D:\claude\_workspace\telemetry\'
}

function Read-InferHubEnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "InferHub env file not found: $Path"
    }
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
            throw "InferHub env file must define $requiredName."
        }
    }
    return $map
}

$codexCommand = Get-Command codex -CommandType Application -ErrorAction Stop
$gitCommand = Get-Command git -CommandType Application -ErrorAction Stop
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).Path
$repositoryRoot = & $gitCommand.Source -C $resolvedWorkingDirectory rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repositoryRoot)) {
    throw "Working directory is not inside a Git repository: $resolvedWorkingDirectory"
}
$repositoryRoot = (Resolve-Path -LiteralPath $repositoryRoot.Trim() -ErrorAction Stop).Path

# Staff / cheap-builder default: full privileges, no approval prompts (matches Astra / Hades launchers).
# Codex CLI 0.156.x sandbox enum: read-only | workspace-write | danger-full-access.
$codexArguments = [System.Collections.Generic.List[string]]::new()
$codexArguments.Add('exec') | Out-Null
$codexArguments.Add('--sandbox') | Out-Null
$codexArguments.Add('danger-full-access') | Out-Null
$codexArguments.Add('--config') | Out-Null
$codexArguments.Add('approval_policy=never') | Out-Null
$codexArguments.Add('--json') | Out-Null
$codexArguments.Add('--cd') | Out-Null
$codexArguments.Add($repositoryRoot) | Out-Null
$codexArguments.Add('--model') | Out-Null
$codexArguments.Add($Model) | Out-Null

$otel = $null
$receiptDir = $null
$eventsPath = $null
$stderrPath = $null
$effectiveModelRoute = if ($ModelRoute) { $ModelRoute } else { $Model }

if ($InferHub) {
    $envMap = Read-InferHubEnvFile -Path $InferHubEnvFile
    $env:INFERHUB_API_KEY = $envMap['INFERHUB_API_KEY']
    $env:INFERHUB_API_URL = $envMap['INFERHUB_API_URL']

    if (-not $ReceiptRoot) {
        $ReceiptRoot = Join-Path $PSScriptRoot '.ire\codex-harness-receipts'
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $receiptDir = Join-Path $ReceiptRoot $stamp
    New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
    $eventsPath = Join-Path $receiptDir 'codex-events.jsonl'
    $stderrPath = Join-Path $receiptDir 'codex-stderr.txt'
    $finalPath = Join-Path $receiptDir 'final-message.md'

    $telemetryPath = Resolve-CodexHarnessTelemetryScript -Override $TelemetryScript
    . $telemetryPath
    $otel = Start-AstraTelemetry -Action 'harness' -TaskId $TaskId -Topology $Topology -Harness 'codex' -CorrelationId "codex-harness-$stamp" -ReceiptDir $receiptDir -ModelRoute $effectiveModelRoute -LlmProvider 'inferhub' -LlmBaseUrlHost $LlmBaseUrlHost
    $otelCodexArgs = Get-AstraCodexOtelArgs

    $providerArgs = @(
        '--config', 'model_provider="inferhub"',
        '--config', 'model_providers.inferhub.name="InferHub"',
        '--config', ('model_providers.inferhub.base_url="{0}"' -f $envMap['INFERHUB_API_URL'].TrimEnd('/')),
        '--config', 'model_providers.inferhub.env_key="INFERHUB_API_KEY"',
        '--config', 'model_providers.inferhub.wire_api="responses"',
        '--config', 'shell_environment_policy.inherit=all'
    )
    foreach ($a in ($providerArgs + $otelCodexArgs)) { $codexArguments.Add($a) | Out-Null }
    $codexArguments.Add('--output-last-message') | Out-Null
    $codexArguments.Add($finalPath) | Out-Null
}

$codexArguments.Add($Prompt) | Out-Null

$codexExitCode = $null
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($InferHub -and $eventsPath -and $stderrPath) {
        & $codexCommand.Source @($codexArguments.ToArray()) 2>> $stderrPath | Tee-Object -FilePath $eventsPath
    } else {
        & $codexCommand.Source @($codexArguments.ToArray())
    }
    $codexExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
} finally {
    if ($otel) {
        Stop-AstraTelemetry -State $otel -ExitCode $codexExitCode -StderrPath $stderrPath -EventsPath $eventsPath
    }
    if ($InferHub) {
        Remove-Item Env:INFERHUB_API_KEY -ErrorAction SilentlyContinue
    }
}

if ($InferHub -and $receiptDir) {
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
}

if ($null -eq $codexExitCode) {
    throw 'Codex did not return a process exit code.'
}
exit $codexExitCode
