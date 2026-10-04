param([string]$Port = 'COM8', [string]$Esphome = $env:JARVIS_ESPHOME)
& "$PSScriptRoot\scripts\esphome.ps1" -Action logs -Port $Port -Esphome $Esphome
exit $LASTEXITCODE
