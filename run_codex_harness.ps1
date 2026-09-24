[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [string]$WorkingDirectory = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'

$codexCommand = Get-Command codex -CommandType Application -ErrorAction Stop
$gitCommand = Get-Command git -CommandType Application -ErrorAction Stop
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).Path
$repositoryRoot = & $gitCommand.Source -C $resolvedWorkingDirectory rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repositoryRoot)) {
    throw "Working directory is not inside a Git repository: $resolvedWorkingDirectory"
}
$repositoryRoot = (Resolve-Path -LiteralPath $repositoryRoot.Trim() -ErrorAction Stop).Path

$codexArguments = @(
    'exec',
    '--sandbox', 'workspace-write',
    '--config', 'approval_policy=never',
    '--config', 'sandbox_workspace_write.network_access=true',
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
