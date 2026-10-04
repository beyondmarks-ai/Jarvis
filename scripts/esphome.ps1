param(
    [Parameter(Mandatory=$true)][ValidateSet('config','compile','upload','logs')][string]$Action,
    [string]$Port = 'COM8',
    [string]$Esphome = $env:JARVIS_ESPHOME
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (!$Esphome) {
    $localExe = Join-Path $repoRoot '.venv\Scripts\esphome.exe'
    if (Test-Path -LiteralPath $localExe) { $Esphome = $localExe }
    else {
        $command = Get-Command esphome -ErrorAction SilentlyContinue
        if ($command) { $Esphome = $command.Source }
    }
}
if (!$Esphome) { throw 'Install requirements-dev.txt, or set JARVIS_ESPHOME to the ESPHome executable.' }
if (!(Test-Path -LiteralPath (Join-Path $repoRoot 'secrets.yaml'))) {
    throw 'Copy secrets.example.yaml to secrets.yaml and fill in your private settings first.'
}
$previousMsystem = $env:MSYSTEM
try {
    Remove-Item Env:\MSYSTEM -ErrorAction SilentlyContinue
    $arguments = @($Action, (Join-Path $repoRoot 'jarvis_voice.yaml'))
    if ($Action -in @('upload','logs')) { $arguments += @('--device', $Port) }
    & $Esphome @arguments
    $result = $LASTEXITCODE
} finally {
    if ($null -ne $previousMsystem) { $env:MSYSTEM = $previousMsystem }
}
exit $result
