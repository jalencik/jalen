# Jalen launcher.
#
# YOU SHOULD RARELY NEED THIS. Since scripts\install_autostart.py was run,
# Jalen starts with Windows and Ctrl+Alt+J wakes him from anywhere. This
# script is for the times you want something specific — text mode,
# diagnostics, a clean restart — and for machines where the hotkey listener
# isn't installed yet.
#
# It exists at all because the obvious thing to type fails twice over:
#   run.py --unmuted      -> PowerShell won't run a script from the current
#                            folder without a ".\" prefix
#   python run.py         -> a fresh terminal's "python" is a DIFFERENT
#                            install with none of the project's packages,
#                            so it dies with "No module named 'numpy'"
#
# This wrapper always uses the project's own interpreter, from any folder.
#
#   .\jalen.ps1              start listening
#   .\jalen.ps1 stop         stop it
#   .\jalen.ps1 restart      stop, then start fresh
#   .\jalen.ps1 status       is it running?
#   .\jalen.ps1 text         type instead of talk
#   .\jalen.ps1 telegram     control from your phone
#   .\jalen.ps1 check        diagnostics
#   .\jalen.ps1 hotkeys      install autostart + the global hotkey
#   .\jalen.ps1 uninstall    remove autostart + the global hotkey

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
$installer = Join-Path $root "scripts\install_autostart.py"
switch ($Command.ToLower()) {
    "start"     { & $py $runner --unmuted }
    "restart"   { & $py $runner --restart --unmuted }
    "stop"      { & $py $runner --stop }
    "status"    { & $py $runner --status }
    "text"      { & $py $runner --text }
    "telegram"  { & $py $runner --telegram }
    "check"     { & $py $runner --check }
    "hotkeys"   { & $py $installer }
    "uninstall" { & $py $installer --remove }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host "Use: start | restart | stop | status | text | telegram | check | hotkeys | uninstall"
        exit 1
    }
}
