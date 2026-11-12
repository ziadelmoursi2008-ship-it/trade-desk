param([string]$Project = (Split-Path -Parent $PSScriptRoot),[switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($Project).TrimEnd([IO.Path]::DirectorySeparatorChar)
$backend = Join-Path $projectRoot 'backend'
if (-not $CheckOnly) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $identity = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))).Replace('-','').Substring(0,12) }
    finally { $sha.Dispose() }
    $task = Get-ScheduledTask -TaskName ('AITradeAgent_Backend_' + $identity) -ErrorAction SilentlyContinue
    if ($task -and $task.Description -eq ('AI Trade Agent: eigener Hintergrund-Backendstart fuer ' + $projectRoot)) { Disable-ScheduledTask -InputObject $task | Out-Null }
    $consolePython = Join-Path $backend 'venv\Scripts\python.exe'
    & $consolePython (Join-Path $PSScriptRoot 'disable_background.py') --project $projectRoot
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Automatik konnte nicht gespeichert werden. Eigener Autostart wurde deaktiviert; Backend wird nach Identitaetspruefung beendet.' }
}
$recordPath = Join-Path $backend 'logs\background-process.json'
if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { Write-Output 'Kein von der Starthilfe angelegter Prozessnachweis vorhanden. Manuell gestartetes Backend im eigenen Terminal beenden.'; return }
$record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath($record.backend) -ne $backend) { throw 'Prozessnachweis gehoert zu einem anderen Projekt.' }
$process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid) -ErrorAction SilentlyContinue
if (-not $process) { Write-Output 'Der aufgezeichnete Backend-Prozess ist bereits beendet.'; return }
$expectedPython = Join-Path $backend 'venv\Scripts\pythonw.exe'
$expectedConfig = Join-Path $PSScriptRoot 'background-logging.json'
if ($process.ExecutablePath -ne $expectedPython -or -not $process.CommandLine -or $process.CommandLine.IndexOf($expectedConfig,[StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw 'Prozessidentitaet passt nicht. Es wurde kein Prozess beendet.'
}
$created = ([datetime]$process.CreationDate).ToUniversalTime()
if ([Math]::Abs(($created-([datetime]$record.created_utc).ToUniversalTime()).TotalSeconds) -gt 2) { throw 'Prozessnummer wurde neu vergeben. Kein Prozess beendet.' }
if ($CheckOnly) { Write-Output 'Eigener Backend-Prozess verifiziert. Keine Aenderung.'; return }
Stop-Process -Id ([int]$record.pid)
Write-Output 'Eigenes Hintergrund-Backend beendet und zugehoeriger Autostart deaktiviert. Zum erneuten automatischen Start Autostart-einrichten.cmd nutzen.'
