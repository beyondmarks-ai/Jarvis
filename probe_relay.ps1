param([int]$ChunkBytes = 3200, [switch]$PaceAudio, [string]$SecretsPath = (Join-Path $PSScriptRoot 'secrets.yaml'))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$audioStream = New-Object System.IO.MemoryStream
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo -ArgumentList 16000,([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen),([System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToAudioStream($audioStream,$format)
$synth.Speak('What is two plus two?')
$audioBytes = $audioStream.ToArray()
$synth.Dispose()
$audioStream.Dispose()
$settings = Get-Content -Raw -LiteralPath $SecretsPath
$relayHost = [regex]::Match($settings, '(?m)^jarvis_realtime_host:\s*["'']?([^"''\r\n]+)').Groups[1].Value.Trim()
$deviceKey = [regex]::Match($settings, '(?m)^jarvis_backend_key:\s*["'']?([^"''\r\n]+)').Groups[1].Value.Trim()
if (!$relayHost -or !$deviceKey) { throw 'Missing relay settings' }
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$cancel = New-Object System.Threading.CancellationTokenSource
$cancel.CancelAfter(45000)
$timer = [Diagnostics.Stopwatch]::StartNew()
$endMs = 0
$firstAudioMs = $null
$receivedAudio = 0
try {
 $ws.Options.SetRequestHeader('X-Jarvis-Key', $deviceKey)
 $null = $ws.ConnectAsync([Uri]('wss://' + $relayHost + '/ws'), $cancel.Token).GetAwaiter().GetResult()
 Write-Output ('Connected after {0} ms' -f $timer.ElapsedMilliseconds)
 $encoded = [Text.Encoding]::UTF8.GetBytes('{"type":"start","language":"en-IN"}')
 $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList (,$encoded)
 $null = $ws.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, $cancel.Token).GetAwaiter().GetResult()
 for ($offset = 0; $offset -lt $audioBytes.Length; $offset += $ChunkBytes) {
  $count = [Math]::Min($ChunkBytes, $audioBytes.Length - $offset)
  $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList $audioBytes,$offset,$count
  $null = $ws.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Binary, $true, $cancel.Token).GetAwaiter().GetResult()
  if ($PaceAudio) { Start-Sleep -Milliseconds ([int]($count / 32)) }
 }
 $encoded = [Text.Encoding]::UTF8.GetBytes('{"type":"end"}')
 $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList (,$encoded)
 $null = $ws.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, $cancel.Token).GetAwaiter().GetResult()
 $endMs = $timer.ElapsedMilliseconds
 Write-Output ('Sent {0} PCM bytes; question ended at {1} ms' -f $audioBytes.Length,$endMs)
 $buffer = New-Object byte[] 8192
 $control = New-Object Text.StringBuilder
 while ($ws.State -eq [Net.WebSockets.WebSocketState]::Open) {
  $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList (,$buffer)
  $result = $ws.ReceiveAsync($segment, $cancel.Token).GetAwaiter().GetResult()
  if ($result.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Text) {
   [void]$control.Append([Text.Encoding]::UTF8.GetString($buffer,0,$result.Count))
   if ($result.EndOfMessage) {
    Write-Output ('+{0} ms: {1}' -f ($timer.ElapsedMilliseconds - $endMs),$control.ToString().Replace($deviceKey,'[REDACTED]'))
    $event = $control.ToString() | ConvertFrom-Json
    [void]$control.Clear()
    if ($event.type -in @('done','error')) { break }
   }
  } elseif ($result.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Binary) {
   if ($null -eq $firstAudioMs) { $firstAudioMs = $timer.ElapsedMilliseconds - $endMs; Write-Output ('First audio: {0} ms after question' -f $firstAudioMs) }
   $receivedAudio += $result.Count
  } else { break }
 }
 Write-Output ('Received audio bytes: ' + $receivedAudio)
 if ($receivedAudio -eq 0) { exit 1 }
} catch { Write-Output $_.Exception.Message; exit 1 } finally { $ws.Dispose(); $cancel.Dispose() }
