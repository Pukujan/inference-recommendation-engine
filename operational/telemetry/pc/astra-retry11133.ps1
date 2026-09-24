# astra-retry11133.ps1 - dot-sourced by launch-astra.ps1 and resume-astra.ps1 (Windows PowerShell 5.1 compatible).
#
# Auto-retry for InferHub model_param_invalid / code 11133 on route cb/gpt-6-astra.
# Approved by Alex 2026-09-24. Diagnosis: an intermittent HTTP 400 from the cb upstream (~1.8% of
# requests, independent of context size) kills the whole codex exec turn. When a turn ends with that
# error, the launcher resumes the SAME thread with a one-line continue prompt, in a new receipt folder.
#
# Rules:
#   - Retry only when exit code != 0 AND the final error/turn.failed event says code 11133 or
#     model_param_invalid. Any other failure is never retried.
#   - At most 2 retries per original launch; a third 11133 stops as before.
#   - Same codex.exe, provider/route config, OTel config and flags as the caller (passed in).
#   - Every decision is appended to RETRY.md in the receipt that was evaluated.
#   - API keys come from the caller's process environment only; nothing here reads or prints them.

$script:Astra11133DefaultMaxRetries = 2
$script:Astra11133RetryPrompt = 'The provider rejected the previous request (transient error 11133). Continue the task from where you left off; do not redo completed tool actions.'

function Get-AstraEventsFailureInfo {
    # Reads a codex --json events file. Returns thread id (first thread.started), the final terminal
    # event type (error / turn.failed / turn.completed) and whether that final failure is code 11133.
    param([Parameter(Mandatory = $true)][string]$EventsPath)
    $info = [pscustomobject]@{
        EventsPath     = $EventsPath
        ThreadId       = $null
        FinalEventType = $null
        Is11133        = $false
        Reason         = ''
    }
    if (-not (Test-Path -LiteralPath $EventsPath)) { $info.Reason = 'events file missing'; return $info }
    $finalLine = $null
    $typeRegex = '^\s*\{\s*"type"\s*:\s*"(thread\.started|error|turn\.failed|turn\.completed)"'
    $wanted = @('thread.started', 'error', 'turn.failed', 'turn.completed')
    foreach ($line in [System.IO.File]::ReadLines($EventsPath)) {
        if (-not ($line.Contains('"thread.started"') -or $line.Contains('"error"') -or $line.Contains('"turn.failed"') -or $line.Contains('"turn.completed"'))) { continue }
        $t = $null
        $m = [regex]::Match($line, $typeRegex)
        if ($m.Success) {
            $t = $m.Groups[1].Value
        } else {
            # Fallback when "type" is not the first key: parse and check the top-level type only.
            try { $po = $line | ConvertFrom-Json; if ($wanted -contains [string]$po.type) { $t = [string]$po.type } } catch {}
        }
        if (-not $t) { continue }
        if ($t -eq 'thread.started') {
            if ($null -eq $info.ThreadId) {
                try { $o = $line | ConvertFrom-Json; if ($o.thread_id) { $info.ThreadId = [string]$o.thread_id } } catch {}
            }
            continue
        }
        $finalLine = $line
        $info.FinalEventType = $t
    }
    if ($null -eq $finalLine) { $info.Reason = 'no terminal event (error/turn.failed/turn.completed) in events file'; return $info }
    if ($info.FinalEventType -eq 'turn.completed') { $info.Reason = 'final event is turn.completed'; return $info }
    $msg = $null
    try {
        $o = $finalLine | ConvertFrom-Json
        if ($info.FinalEventType -eq 'turn.failed') { $msg = [string]$o.error.message } else { $msg = [string]$o.message }
    } catch { $msg = $finalLine }
    if ([string]::IsNullOrEmpty($msg)) { $msg = $finalLine }
    if ($msg -match '"code"\s*:\s*11133\b' -or $msg -match 'model_param_invalid') {
        $info.Is11133 = $true
        $info.Reason = "final $($info.FinalEventType) carries code 11133 / model_param_invalid"
    } else {
        $short = $msg; if ($short.Length -gt 200) { $short = $short.Substring(0, 200) + '...' }
        $info.Reason = "final $($info.FinalEventType) is not 11133: $short"
    }
    return $info
}

function Get-Astra11133RetryDecision {
    # Pure decision: retry only on non-zero exit + final 11133 + known thread + budget left.
    param($Info, $ExitCode, [int]$RetriesUsed, [int]$MaxRetries = 2)
    if ($null -ne $ExitCode -and "$ExitCode" -eq '0') { return [pscustomobject]@{ Retry = $false; Reason = 'exit code 0' } }
    if (-not $Info.Is11133) { return [pscustomobject]@{ Retry = $false; Reason = "not code 11133 ($($Info.Reason))" } }
    if (-not $Info.ThreadId) { return [pscustomobject]@{ Retry = $false; Reason = 'code 11133 but no thread.started thread_id in events' } }
    if ($RetriesUsed -ge $MaxRetries) { return [pscustomobject]@{ Retry = $false; Reason = "code 11133 but retry budget exhausted ($RetriesUsed of $MaxRetries used)" } }
    return [pscustomobject]@{ Retry = $true; Reason = "code 11133; retry $($RetriesUsed + 1) of $MaxRetries" }
}

function Write-AstraRetryNote {
    param([string]$ReceiptDir, [string[]]$Lines)
    try { Add-Content -LiteralPath (Join-Path $ReceiptDir 'RETRY.md') -Value (($Lines + '') -join [Environment]::NewLine) -Encoding UTF8 } catch {}
}

function New-AstraRetryReceiptDir {
    param([Parameter(Mandatory = $true)][string]$ReceiptRoot)
    for ($i = 0; $i -lt 30; $i++) {
        $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
        $dir = Join-Path $ReceiptRoot $stamp
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            return [pscustomobject]@{ Stamp = $stamp; Dir = $dir }
        }
        Start-Sleep -Seconds 1
    }
    throw 'Could not allocate a unique retry receipt directory.'
}

function Invoke-AstraResumeRetry {
    # One codex exec resume of $ThreadId in $Receipt.Dir. Returns only the codex exit code.
    param(
        [string]$CodexExe, [string[]]$ProviderConfigArgs, [string]$WorkingDirectory, $Receipt,
        [string]$ThreadId, [string]$Model, [string]$TaskId, [string]$Topology,
        [int]$Attempt, [int]$MaxRetries, [string]$FailedReceiptDir, [string]$OriginReceiptDir
    )
    $dir = $Receipt.Dir
    $stamp = $Receipt.Stamp
    $eventsPath = Join-Path $dir 'codex-events.jsonl'
    $finalPath = Join-Path $dir 'final-message.md'
    $stderrPath = Join-Path $dir 'codex-stderr.txt'
    $failedLeaf = Split-Path $FailedReceiptDir -Leaf
    $originLeaf = Split-Path $OriginReceiptDir -Leaf
    $attemptLines = @(
        "action=codex-exec-resume-retry11133"
        "session_id=$ThreadId"
        "reason=auto-retry after InferHub model_param_invalid code 11133"
        "retry_attempt=$Attempt of $MaxRetries"
        "failed_receipt=$failedLeaf"
        "origin_receipt=$originLeaf"
        "model=$Model"
        "working_directory=$WorkingDirectory"
        "prompt=inline retry prompt (see RETRY.md)"
        "started_utc=$stamp"
        "launcher_pid=$PID"
        "stderr=$stderrPath"
    )
    [System.IO.File]::WriteAllText((Join-Path $dir 'attempt.txt'), ($attemptLines -join [Environment]::NewLine) + [Environment]::NewLine)
    [System.IO.File]::WriteAllText((Join-Path $dir 'pid.txt'), "$PID$([Environment]::NewLine)")
    Write-AstraRetryNote $dir @(
        "# Auto-retry $Attempt of $MaxRetries after InferHub code 11133"
        ''
        "- failed_receipt: $failedLeaf"
        "- origin_receipt: $originLeaf (first launch of this chain)"
        "- thread_id resumed: $ThreadId"
        "- model: $Model"
        "- started_utc: $stamp"
        "- launcher_pid: $PID"
        "- prompt: $script:Astra11133RetryPrompt"
        "- policy: launcher auto-resume on 11133 only, max 2 per original launch (approved by Alex 2026-09-24)"
    )

    $otel = Start-AstraTelemetry -Action 'resume' -TaskId $TaskId -Topology $Topology -CorrelationId "astra-$stamp" -ReceiptDir $dir -ModelRoute $Model
    $otelArgs = Get-AstraCodexOtelArgs
    $codexArgs = @('exec', '--sandbox', 'danger-full-access', '--cd', $WorkingDirectory) + $ProviderConfigArgs + $otelArgs + @(
        'resume', '--json', '--model', $Model, '--output-last-message', $finalPath, $ThreadId, '-'
    )
    $exitCode = $null
    try {
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $script:Astra11133RetryPrompt | & $CodexExe @codexArgs 2> $stderrPath | Tee-Object -FilePath $eventsPath | Out-Host
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
    } finally {
        if ($null -eq $exitCode) { $exitCode = $LASTEXITCODE }
        $summary = @(
            "exit_code=$exitCode"
            "action=codex-exec-resume-retry11133"
            "session_id=$ThreadId"
            "retry_attempt=$Attempt of $MaxRetries"
            "failed_receipt=$failedLeaf"
            "origin_receipt=$originLeaf"
            "model=$Model"
            "provider=inferhub"
            "sandbox=danger-full-access"
            "approval_policy=never"
            "network_access=true"
            "working_directory=$WorkingDirectory"
            "events=$eventsPath"
            "stderr=$stderrPath"
            "final_message=$finalPath"
            "trace_id=$($otel.trace_id)"
        ) -join [Environment]::NewLine
        try { [System.IO.File]::WriteAllText((Join-Path $dir 'summary.txt'), $summary + [Environment]::NewLine) } catch {}
        Stop-AstraTelemetry -State $otel -ExitCode $exitCode -StderrPath $stderrPath
    }
    return $exitCode
}

function Invoke-Astra11133RetryChain {
    # Call after the original codex exec finished. Returns the final exit code of the chain.
    param(
        [Parameter(Mandatory = $true)][string]$CodexExe,
        [Parameter(Mandatory = $true)][string[]]$ProviderConfigArgs,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ReceiptRoot,
        [Parameter(Mandatory = $true)][string]$FailedReceiptDir,
        $ExitCode,
        [string]$Model = 'cb/gpt-6-astra',
        [string]$TaskId = 'pcm-astra-owner',
        [string]$Topology = 'main',
        [int]$MaxRetries = 2
    )
    $currentDir = $FailedReceiptDir
    $currentExit = $ExitCode
    $retriesUsed = 0
    while ($true) {
        if ($null -ne $currentExit -and "$currentExit" -eq '0') { return $currentExit }
        $info = Get-AstraEventsFailureInfo -EventsPath (Join-Path $currentDir 'codex-events.jsonl')
        $decision = Get-Astra11133RetryDecision -Info $info -ExitCode $currentExit -RetriesUsed $retriesUsed -MaxRetries $MaxRetries
        $note = @(
            "## Retry decision $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))"
            ''
            "- exit_code: $currentExit"
            "- final_event: $($info.FinalEventType)"
            "- thread_id: $($info.ThreadId)"
            "- is_11133: $($info.Is11133)"
            "- origin_receipt: $(Split-Path $FailedReceiptDir -Leaf)"
            "- retries_used_before_decision: $retriesUsed of $MaxRetries"
        )
        if (-not $decision.Retry) {
            Write-AstraRetryNote $currentDir ($note + "- decision: NO RETRY - $($decision.Reason). Launcher exits with code $currentExit as before.")
            return $currentExit
        }
        $retriesUsed++
        $receipt = New-AstraRetryReceiptDir -ReceiptRoot $ReceiptRoot
        Write-AstraRetryNote $currentDir ($note + "- decision: RETRY $retriesUsed of $MaxRetries - codex exec resume $($info.ThreadId) in new receipt $($receipt.Stamp)")
        $result = Invoke-AstraResumeRetry -CodexExe $CodexExe -ProviderConfigArgs $ProviderConfigArgs -WorkingDirectory $WorkingDirectory `
            -Receipt $receipt -ThreadId $info.ThreadId -Model $Model -TaskId $TaskId -Topology $Topology `
            -Attempt $retriesUsed -MaxRetries $MaxRetries -FailedReceiptDir $currentDir -OriginReceiptDir $FailedReceiptDir
        $currentExit = @($result)[-1]
        $currentDir = $receipt.Dir
    }
}