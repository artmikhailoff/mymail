# Full daily mymail run for the 08:00 scheduled task.
# Invokes: python -u scripts/mymail.py daily
# Does not pass --safe, --skip-clean, or --skip-email.
# Stdout and stderr are appended to data/logs/morning-yyyy-MM-dd.log.

$ErrorActionPreference = 'Continue'

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $RepoRoot

$python = 'python'
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
}

$logDir = Join-Path $RepoRoot 'data\logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$stamp = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $logDir ("morning-$stamp.log")
$outFile = Join-Path $logDir ("morning-$stamp.stdout.tmp")
$errFile = Join-Path $logDir ("morning-$stamp.stderr.tmp")

$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$header = '===== {0} =====' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
[System.IO.File]::AppendAllText($logFile, $header + [Environment]::NewLine, $utf8)

# Start-Process keeps a reliable ExitCode on Windows PowerShell 5.1.
$proc = Start-Process -FilePath $python `
    -ArgumentList @('-u', 'scripts/mymail.py', 'daily') `
    -WorkingDirectory $RepoRoot `
    -NoNewWindow `
    -Wait `
    -PassThru `
    -RedirectStandardOutput $outFile `
    -RedirectStandardError $errFile

$exitCode = $proc.ExitCode

foreach ($part in @($outFile, $errFile)) {
    if (Test-Path -LiteralPath $part) {
        $text = [System.IO.File]::ReadAllText($part, $utf8)
        if ($text.Length -gt 0) {
            [System.IO.File]::AppendAllText($logFile, $text, $utf8)
            if (-not $text.EndsWith("`n") -and -not $text.EndsWith("`r`n")) {
                [System.IO.File]::AppendAllText($logFile, [Environment]::NewLine, $utf8)
            }
            Write-Output $text
        }
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    }
}

$footer = '===== exit {0} {1} =====' -f $exitCode, (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
[System.IO.File]::AppendAllText($logFile, $footer + [Environment]::NewLine, $utf8)

exit $exitCode
