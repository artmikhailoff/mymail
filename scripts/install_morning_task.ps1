# Register the daily 08:00 mymail morning task (idempotent via -Force).
#
# Principal LogonType is Interactive: the task runs only while this user is
# logged on. Gmail app-password use expects that interactive session.
# RunLevel is Limited — highest privileges are not required.
#
# Settings: StartWhenAvailable (a missed 08:00 run starts when the machine
# is available) and WakeToRun (wake the computer to run).

$ErrorActionPreference = 'Stop'

$TaskName = 'mymail-morning'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$MorningScript = Join-Path $RepoRoot 'scripts\morning.ps1'

if (-not (Test-Path -LiteralPath $MorningScript)) {
    Write-Error "Missing morning script: $MorningScript"
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powershell)) {
    $powershell = 'powershell.exe'
}

$argument = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $MorningScript
$action = New-ScheduledTaskAction -Execute $powershell -Argument $argument -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '08:00'

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun

# Interactive token: user must be logged on. Do not store a password.
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

$description = 'Full mymail daily run at 08:00 local (sync, clean, digests, email). Interactive logon; user must be logged on.'

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

Write-Output "Registered scheduled task: $TaskName"
Write-Output "Daily 08:00 local. LogonType Interactive (user must be logged on). StartWhenAvailable and WakeToRun are enabled."
Write-Output "Verify: Get-ScheduledTask -TaskName '$TaskName'"
Write-Output "        (Get-ScheduledTask -TaskName '$TaskName').Settings | Format-List StartWhenAvailable, WakeToRun"
Write-Output "        (Get-ScheduledTask -TaskName '$TaskName').Principal | Format-List UserId, LogonType, RunLevel"
Write-Output "        (Get-ScheduledTask -TaskName '$TaskName').Triggers | Format-List"
