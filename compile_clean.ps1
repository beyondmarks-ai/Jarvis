param([string]$Esphome = $env:JARVIS_ESPHOME)
& "$PSScriptRoot\scripts\esphome.ps1" -Action compile -Esphome $Esphome
exit $LASTEXITCODE
