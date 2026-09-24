[CmdletBinding()]
param(
    [string]$SessionId = '01a0d488-bef3-72e0-88bf-8684fb9da197',
    [string]$PromptFile = 'D:\claude\_workspace\pcm-astra-owner\return-result-04.md',
    [string]$WorkingDirectory = 'D:\claude\projects\project-continuity-modules',
    [string]$ReceiptRoot = 'D:\claude\_workspace\pcm-astra-owner\receipts',
    [string]$Reason = 'return staff result for Astra review',
    [string]$TaskId = 'pcm-astra-owner',
    [ValidateSet('main', 'subagent')][string]$Topology = 'main'
)

$ErrorActionPreference = 'Stop'

$codexExe = 'C:\Users\pujan\AppData\Local\OpenAI\Codex\bin\247581e40ee272fb\codex.exe'
if (-not (Test-Path -LiteralPath $codexExe)) {
    throw "Codex executable is missing: $codexExe"
}
if (-not (Test-Path -LiteralPath $PromptFile)) {
    throw "Prompt file is missing: $PromptFile"
}

$envFile = 'D:\claude\inferhub\.env'
$envMap = @{}
foreach ($rawLine in [System.IO.File]::ReadAllLines($envFile)) {
    if ($rawLine -match '^\s*(INFERHUB_API_KEY|INFERHUB_API_URL)\s*=\s*(.*?)\s*$') {
        $value = $Matches[2]
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $envMap[$Matches[1]] = $value
    }
}
foreach ($requiredName in @('INFERHUB_API_KEY', 'INFERHUB_API_URL')) {
    if (-not $envMap.ContainsKey($requiredName) -or [string]::IsNullOrWhiteSpace($envMap[$requiredName])) {
        throw "InferHub environment file must define $requiredName."
    }
}

$env:INFERHUB_API_KEY = $envMap['INFERHUB_API_KEY']
$env:INFERHUB_API_URL = $envMap['INFERHUB_API_URL']
$ghToken = (& gh auth token | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($ghToken)) {
    throw 'gh auth token was empty. Run gh auth login before resuming Astra.'
}
$env:GH_TOKEN = $ghToken
$env:GITHUB_TOKEN = $ghToken

$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$receiptDir = Join-Path $ReceiptRoot $stamp
New-Item -ItemType Directory -Path $receiptDir -Force | Out-Null
$promptText = [System.IO.File]::ReadAllText($PromptFile)
$eventsPath = Join-Path $receiptDir 'codex-events.jsonl'
$finalPath = Join-Path $receiptDir 'final-message.md'
$attemptPath = Join-Path $receiptDir 'attempt.txt'
$stderrPath = Join-Path $receiptDir 'codex-stderr.txt'
$attempt = @(
    "action=codex-exec-resume"
    "session_id=$SessionId"
    "reason=$Reason"
    "working_directory=$WorkingDirectory"
    "prompt_file=$PromptFile"
    "started_utc=$stamp"
    "launcher=persistent-background"
    "bugfix=no-caller-timeout"
    "stderr=$stderrPath"
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText($attemptPath, $attempt + [Environment]::NewLine)
[System.IO.File]::WriteAllText((Join-Path $receiptDir 'pid.txt'), "$PID$([Environment]::NewLine)")

# Telemetry (IRE issue 40): root span per run + TRACEPARENT/OTEL_RESOURCE_ATTRIBUTES for the Codex child.
. 'D:\claude\_workspace\telemetry\astra-telemetry.ps1'
$otel = Start-AstraTelemetry -Action 'resume' -TaskId $TaskId -Topology $Topology -CorrelationId "astra-$stamp" -ReceiptDir $receiptDir -ModelRoute 'cb/gpt-6-astra'
$otelCodexArgs = Get-AstraCodexOtelArgs

# Provider/route config, shared with the code-11133 auto-retry (astra-retry11133.ps1) so retries use identical settings.
$astraProviderConfigArgs = @(
    '--config', 'approval_policy="never"',
    '--config', 'sandbox_workspace_write.network_access=true',
    '--config', 'shell_environment_policy.inherit=all',
    '--config', 'model_provider="inferhub"',
    '--config', 'model_providers.inferhub.name="InferHub"',
    '--config', 'model_providers.inferhub.base_url="https://api.inferhub.dev/v1"',
    '--config', 'model_providers.inferhub.env_key="INFERHUB_API_KEY"',
    '--config', 'model_providers.inferhub.wire_api="responses"'
)
$codexArguments = @(
    'exec',
    '--sandbox', 'danger-full-access',
    '--cd', $WorkingDirectory
) + $astraProviderConfigArgs + $otelCodexArgs + @(
    'resume',
    '--json',
    '--model', 'cb/gpt-6-astra',
    '--output-last-message', $finalPath,
    $SessionId,
    '-'
)

$codexExitCode = $null
try {
    # PowerShell 5.1 turns native stderr into a terminating NativeCommandError when
    # ErrorActionPreference is Stop. Codex writes a non-fatal models-catalog warning
    # there, so capture it without failing the resume.
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $promptText | & $codexExe @codexArguments 2> $stderrPath | Tee-Object -FilePath $eventsPath
    $codexExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
} finally {
    if ($null -eq $codexExitCode) {
        $codexExitCode = $LASTEXITCODE
    }
    $summary = @(
        "exit_code=$codexExitCode"
        "action=codex-exec-resume"
        "session_id=$SessionId"
        "model=cb/gpt-6-astra"
        "provider=inferhub"
        "sandbox=danger-full-access"
        "approval_policy=never"
        "network_access=true"
        "working_directory=$WorkingDirectory"
        "events=$eventsPath"
        "stderr=$stderrPath"
        "final_message=$finalPath"
        "launcher=persistent-background"
        "bugfix=no-caller-timeout"
        "trace_id=$($otel.trace_id)"
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText((Join-Path $receiptDir 'summary.txt'), $summary + [Environment]::NewLine)
    Stop-AstraTelemetry -State $otel -ExitCode $codexExitCode -StderrPath $stderrPath
}
# Auto-retry on InferHub model_param_invalid / code 11133 (approved by Alex 2026-09-24): resume the SAME thread
# in a new receipt folder, max 2 retries per original launch. Other failures are never retried. See RETRY.md.
if ($null -ne $codexExitCode -and $codexExitCode -ne 0) {
    try {
        . 'D:\claude\_workspace\pcm-astra-owner\astra-retry11133.ps1'
        $codexExitCode = @(Invoke-Astra11133RetryChain -CodexExe $codexExe -ProviderConfigArgs $astraProviderConfigArgs -WorkingDirectory $WorkingDirectory -ReceiptRoot $ReceiptRoot -FailedReceiptDir $receiptDir -ExitCode $codexExitCode -Model 'cb/gpt-6-astra' -TaskId $TaskId -Topology $Topology)[-1]
    } catch {
        try { Add-Content -LiteralPath (Join-Path $receiptDir 'RETRY.md') -Value "retry helper error (no further retry): $($_.Exception.Message)" } catch {}
    }
}
if ($null -eq $codexExitCode) {
    throw 'Codex did not return a process exit code.'
}
exit $codexExitCode
