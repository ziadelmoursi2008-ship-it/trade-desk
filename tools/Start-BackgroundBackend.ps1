param(
    [string]$Project = (Split-Path -Parent $PSScriptRoot),
    [switch]$Wait,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($Project).TrimEnd([IO.Path]::DirectorySeparatorChar)
$backend = Join-Path $projectRoot 'backend'
$python = Join-Path $backend 'venv\Scripts\pythonw.exe'
$logConfig = Join-Path $PSScriptRoot 'background-logging.json'
foreach ($required in @($python, $logConfig, (Join-Path $backend 'main.py'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Datei fehlt: $required" }
}
$sha = [Security.Cryptography.SHA256]::Create()
try { $projectId = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($backend.ToLowerInvariant())))).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
if ($CheckOnly) {
    Write-Output "Startpruefung erfolgreich. Projekt: $projectRoot. Kein Prozess gestartet."
    return
}
function Own-Backend {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        return ($health.background_supported -eq $true -and $health.project_id -eq $projectId)
    } catch { return $false }
}
if (Own-Backend) { Write-Output 'Das Backend dieses Projekts laeuft bereits.'; return }
$socket = [Net.Sockets.TcpClient]::new()
try {
    try { $null = $socket.ConnectAsync('127.0.0.1',8000).Wait(500) } catch { }
    if ($socket.Connected) { throw 'Port 8000 ist belegt. Vorhandenes Backend zuerst beenden; kein fremder Prozess wird beendet.' }
} finally { $socket.Dispose() }
$logs = Join-Path $backend 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$arguments = @('-m','uvicorn','main:app','--host','127.0.0.1','--port','8000','--log-config',('"' + $logConfig + '"'))
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $backend -WindowStyle Hidden -PassThru
$record = @{ pid=$process.Id; backend=$backend; created_utc=$process.StartTime.ToUniversalTime().ToString('o'); python=$python; config=$logConfig }
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logs 'background-process.json') -Encoding UTF8
if ($Wait) {
    $process.WaitForExit()
    exit $process.ExitCode
}
$ready = $false
for ($attempt=0; $attempt -lt 40; $attempt++) {
    if (Own-Backend) { $ready=$true; break }
    if ($process.HasExited) { break }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { throw "Backend noch nicht erreichbar. Protokoll: $logs\background-backend.log" }
Write-Output 'Backend laeuft ohne sichtbares Terminal. Dauerbetrieb in der Website einschalten.'
