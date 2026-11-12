param(
    [string]$Project = (Split-Path -Parent $PSScriptRoot),
    [switch]$Remove,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($Project).TrimEnd([IO.Path]::DirectorySeparatorChar)
$helper = Join-Path $PSScriptRoot 'Start-BackgroundBackend.ps1'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Backend-Starthilfe fehlt.' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $identity = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot.ToLowerInvariant())))).Replace('-','').Substring(0,12) }
finally { $sha.Dispose() }
$name = 'AITradeAgent_Backend_' + $identity
$description = 'AI Trade Agent: eigener Hintergrund-Backendstart fuer ' + $projectRoot
if ($CheckOnly) { Write-Output "Aufgabenpruefung erfolgreich: $name. Keine Windows-Aufgabe angelegt oder geaendert."; return }
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) { throw 'Gleichnamige Aufgabe hat eine andere Herkunft; keine Aenderung vorgenommen.' }
if ($Remove) {
    if ($existing) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
    Write-Output 'Autostart entfernt. Ein schon laufendes Backend bleibt bis zum separaten Beenden aktiv.'
    return
}
$powershell = Join-Path $PSHOME 'powershell.exe'
$arguments = '-NoProfile -WindowStyle Hidden -File "' + $helper + '" -Project "' + $projectRoot + '" -Wait'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $projectRoot
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$login = New-ScheduledTaskTrigger -AtLogOn -User $user
$watch = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger @($login,$watch) -Principal $principal -Settings $settings -Description $description
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output 'Backend-Autostart nach Windows-Anmeldung eingerichtet. Keine Energieeinstellung geaendert.'
Write-Output 'Die Aufgabe startet nur das Backend. Gemini/Python-Automatik wird in der Website separat eingeschaltet.'
