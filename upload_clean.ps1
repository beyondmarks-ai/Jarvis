param([string]$Port = 'COM8', [string]$Esphome = $env:JARVIS_ESPHOME)
& "$PSScriptRoot\scripts\esphome.ps1" -Action upload -Port $Port -Esphome $Esphome
exit $LASTEXITCODE
