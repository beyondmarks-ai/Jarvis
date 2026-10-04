param([int]$Seconds = 45, [string]$Port = 'COM8')
$serial = New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$serial.DtrEnable = $false
$serial.RtsEnable = $false
try {
 $serial.Open()
 $timer = [Diagnostics.Stopwatch]::StartNew()
 while ($timer.Elapsed.TotalSeconds -lt $Seconds) {
  if ($serial.BytesToRead -gt 0) {
   $chunk = $serial.ReadExisting() -replace '\x1B\[[0-9;]*[A-Za-z]', ''
   Write-Output ($chunk -replace '(?i)((?:api.?key|access.?token|password|authorization|secret)\s*[:=]\s*)\S+', '$1[REDACTED]')
  }
  Start-Sleep -Milliseconds 100
 }
} finally { if ($serial.IsOpen) { $serial.Close() }; $serial.Dispose() }
