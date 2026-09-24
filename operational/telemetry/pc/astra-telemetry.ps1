# astra-telemetry.ps1 - dot-sourced by the Astra launchers (launch-astra.ps1 / resume-astra.ps1).
# Emits one root span per run to the gravebuster collector via astra_otel.py (stdlib Python, no daemon),
# exports TRACEPARENT + OTEL_RESOURCE_ATTRIBUTES to the Codex child so its spans nest under the root,
# and builds Codex --config overrides for OTLP/HTTP export. Every function is fail-open: telemetry
# problems are written to the receipt dir and never stop or change the outcome of a run.
# Added 2026-09-24 (IRE issue 40). Remove the dot-source line in a launcher to disable.
# 2026-09-24 (IRE #41): Stop-AstraTelemetry also passes the receipt dir so provider errors reach the root span.

$script:AstraOtelHelper = Join-Path $PSScriptRoot 'astra_otel.py'
if (-not $script:AstraOtelHelper -or -not (Test-Path -LiteralPath $script:AstraOtelHelper)) {
    $script:AstraOtelHelper = 'D:\claude\_workspace\telemetry\astra_otel.py'
}
$script:AstraOtlpBase = 'http://100.93.66.34:4318'
$script:AstraOtelEnvironment = 'pcm-astra'

function ConvertTo-AstraNativeArg([string]$Value) {
    # Windows PowerShell 5.1 (and 7.x in Legacy mode) strips embedded double quotes when calling
    # native executables, so escape them; PowerShell 7.3+ Standard/Windows mode passes them intact.
    $v = $PSVersionTable.PSVersion
    $legacy = ($v.Major -lt 7) -or ($v.Major -eq 7 -and $v.Minor -lt 3) -or
              ((Get-Variable -Name PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue) -eq 'Legacy')
    if ($legacy) { return $Value.Replace('"', '\"') }
    return $Value
}

function Get-AstraCodexOtelArgs {
    $b = $script:AstraOtlpBase
    $raw = @(
        "otel.environment=""$script:AstraOtelEnvironment""",
        'otel.log_user_prompt=false',
        "otel.exporter={otlp-http={endpoint=""$b/v1/logs"",protocol=""binary""}}",
        "otel.trace_exporter={otlp-http={endpoint=""$b/v1/traces"",protocol=""binary""}}"
    )
    $out = @()
    foreach ($r in $raw) { $out += '--config'; $out += (ConvertTo-AstraNativeArg $r) }
    return ,$out
}

function Start-AstraTelemetry {
    param(
        [string]$Action = 'launch',
        [string]$TaskId = 'pcm-astra-owner',
        [ValidateSet('main', 'subagent')][string]$Topology = 'main',
        [string]$Harness = 'codex',
        [string]$CorrelationId = '',
        [string]$ReceiptDir = '',
        [string]$ModelRoute = 'cb/gpt-6-astra',
        [string]$LlmProvider = 'inferhub',
        [string]$LlmBaseUrlHost = 'api.inferhub.dev'
    )
    $ErrorActionPreference = 'Continue'   # native stderr must never become terminating here (PS 5.1)
    $state = [pscustomobject]@{ trace_id = $null; span_id = $null; start_ns = $null; traceparent = $null; action = $Action; receipt_dir = $ReceiptDir; attrs = @() }
    try {
        $res = "harness=$Harness,topology=$Topology,task.id=$TaskId,ire.issue=40,deployment.environment=$script:AstraOtelEnvironment"
        if ($CorrelationId) { $res += ",correlation_id=$CorrelationId" }
        # InferHub scoping (2026-09-24): the gravebuster collector keeps ONLY llm.provider=inferhub.
        $res += ",llm.provider=$LlmProvider,llm.base_url_host=$LlmBaseUrlHost,llm.model_route=$ModelRoute"
        $env:OTEL_RESOURCE_ATTRIBUTES = $res
        $env:OTEL_EXPORTER_OTLP_ENDPOINT = $script:AstraOtlpBase
        $env:OTEL_EXPORTER_OTLP_PROTOCOL = 'http/protobuf'
        $state.attrs = @('--attr', "run.action=$Action", '--attr', "run.harness=$Harness", '--attr', "run.topology=$Topology", '--attr', "task.id=$TaskId")
        $json = (& python $script:AstraOtelHelper begin --service astra-launcher --name "astra.$Action" @($state.attrs) 2>$null | Out-String).Trim()
        if ($json) {
            $o = $json | ConvertFrom-Json
            $state.trace_id = $o.trace_id; $state.span_id = $o.span_id; $state.start_ns = $o.start_ns; $state.traceparent = $o.traceparent
            $env:TRACEPARENT = $o.traceparent
        }
    } catch {
        if ($ReceiptDir) { try { Add-Content -LiteralPath (Join-Path $ReceiptDir 'telemetry-warning.txt') -Value "start: $($_.Exception.Message)" } catch {} }
    }
    return $state
}

function Stop-AstraTelemetry {
    param($State, $ExitCode, [string]$StderrPath = '', [string]$EventsPath = '', [switch]$TimedOut)
    $ErrorActionPreference = 'Continue'
    try {
        if (-not $State -or -not $State.trace_id) { return }
        $a = @('end', '--service', 'astra-launcher', '--name', "astra.$($State.action)",
               '--trace-id', $State.trace_id, '--span-id', $State.span_id, '--start-ns', "$($State.start_ns)",
               '--exit-code', "$ExitCode") + @($State.attrs)
        if ($StderrPath -and (Test-Path -LiteralPath $StderrPath)) { $a += @('--stderr-file', $StderrPath) }
        # Provider-error capture (IRE #41 / #40 M0.6): astra_otel.py scans codex-events.jsonl, codex-stderr.txt
        # and launcher-error.txt for HTTP status / provider code (e.g. 11133) / request id and puts them on the root span.
        if ($EventsPath -and (Test-Path -LiteralPath $EventsPath)) { $a += @('--events-file', $EventsPath) }
        if ($State.receipt_dir -and (Test-Path -LiteralPath $State.receipt_dir)) { $a += @('--receipt-dir', $State.receipt_dir) }
        if ($TimedOut) { $a += '--timed-out' }
        & python $script:AstraOtelHelper @a 2>$null | Out-Null
    } catch {
        if ($State.receipt_dir) { try { Add-Content -LiteralPath (Join-Path $State.receipt_dir 'telemetry-warning.txt') -Value "stop: $($_.Exception.Message)" } catch {} }
    } finally {
        Remove-Item Env:TRACEPARENT -ErrorAction SilentlyContinue
    }
}
