[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [string]$WorkingDirectory = (Get-Location).Path,

    # Optional path to a private/operational launcher that wraps this harness (receipts, provider
    # config, export). When set, that script is invoked with the same Model/Prompt/WorkingDirectory
    # and this script exits with its status. Keeps provider-specific wiring out of the public root.
    [string]$WrapperScript = ''
)

$ErrorActionPreference = 'Stop'

if ($WrapperScript) {
    if (-not (Test-Path -LiteralPath $WrapperScript)) {
        throw "Wrapper script not found: $WrapperScript"
    }
    & $WrapperScript -Model $Model -Prompt $Prompt -WorkingDirectory $WorkingDirectory
    exit $LASTEXITCODE
}

$codexCommand = Get-Command codex -CommandType Application -ErrorAction Stop
$gitCommand = Get-Command git -CommandType Application -ErrorAction Stop
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).Path
$repositoryRoot = & $gitCommand.Source -C $resolvedWorkingDirectory rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repositoryRoot)) {
    throw "Working directory is not inside a Git repository: $resolvedWorkingDirectory"
}
$repositoryRoot = (Resolve-Path -LiteralPath $repositoryRoot.Trim() -ErrorAction Stop).Path

# Staff / cheap-builder default: full privileges, no approval prompts (matches owner launchers).
# Codex CLI 0.156.x sandbox enum: read-only | workspace-write | danger-full-access.
$codexArguments = @(
    'exec',
    '--sandbox', 'danger-full-access',
    '--config', 'approval_policy=never',
    '--json',
    '--cd', $repositoryRoot,
    '--model', $Model,
    $Prompt
)

& $codexCommand.Source @codexArguments
$codexExitCode = $LASTEXITCODE
if ($null -eq $codexExitCode) {
    throw 'Codex did not return a process exit code.'
}
exit $codexExitCode
