# Jarvis launcher.
#
# Exists because the obvious thing to type fails twice over:
#   run.py --unmuted      -> PowerShell won't run a script from the current
#                            folder without a ".\" prefix
#   python run.py         -> a fresh terminal's "python" is a DIFFERENT
#                            install with none of the project's packages,
#                            so it dies with "No module named 'numpy'"
#
# This wrapper always uses the project's own interpreter, from any folder.
#
#   .\jarvis.ps1              start listening
#   .\jarvis.ps1 stop         stop it
#   .\jarvis.ps1 restart      stop, then start fresh
#   .\jarvis.ps1 status       is it running?
#   .\jarvis.ps1 text         type instead of talk
#   .\jarvis.ps1 telegram     control from your phone
#   .\jarvis.ps1 check        diagnostics

param([Parameter(Position = 0)][string]$Command = "start")

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "The project's Python is missing: $py" -ForegroundColor Red
    Write-Host "Create it with:  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "then:            .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

$runner = Join-Path $root "run.py"
switch ($Command.ToLower()) {
    "start"    { & $py $runner --unmuted }
    "restart"  { & $py $runner --restart --unmuted }
    "stop"     { & $py $runner --stop }
    "status"   { & $py $runner --status }
    "text"     { & $py $runner --text }
    "telegram" { & $py $runner --telegram }
    "check"    { & $py $runner --check }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host "Use: start | restart | stop | status | text | telegram | check"
        exit 1
    }
}
