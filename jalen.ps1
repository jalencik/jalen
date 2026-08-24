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
#   .\jalen.ps1 why          why did it stop last time?
#   .\jalen.ps1 setup        guided first-time setup (keys, Google, Telegram)
#   .\jalen.ps1 todo         what still needs YOU, and what each thing does
#   .\jalen.ps1 can          everything Jalen can do, generated from the code
#   .\jalen.ps1 vault        create your password vault (you type the passphrase)
#   .\jalen.ps1 hands        see what the camera sees, to learn the gesture
#   .\jalen.ps1 voice        teach the wake word your own voice (~15 min)
#   .\jalen.ps1 licence      decide who may use this (records your answer)
#   .\jalen.ps1 rehearse     walk the end-to-end flows that need you present
#   .\jalen.ps1 update       pull, back up, reinstall, run the tests
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
$scripts = Join-Path $root "scripts"
switch ($Command.ToLower()) {
    "start"     { & $py $runner --unmuted }
    "restart"   { & $py $runner --restart --unmuted }
    "stop"      { & $py $runner --stop }
    "status"    { & $py $runner --status }
    "text"      { & $py $runner --text }
    "telegram"  { & $py $runner --telegram }
    "check"     { & $py $runner --check }
    "why"       { & $py $runner --why }
    "setup"     { & $py (Join-Path $scripts "onboard.py") }
    "rehearse"  { & $py (Join-Path $scripts "rehearse.py") }
    "vault"     { & $py (Join-Path $scripts "vault_setup.py") }
    "voice"     { & $py (Join-Path $scripts "record_wake_samples.py") }
    "todo"      { & $py (Join-Path $scripts "whats_left.py") }
    "can"       { & $py (Join-Path $scripts "capabilities.py") }
    "abilities" { & $py (Join-Path $scripts "abilities.py") }
    "disk"      { & $py (Join-Path $scripts "disk_audit.py") }
    "ready"     { & $py (Join-Path $scripts "readiness.py") }
    "accept"    { & $py (Join-Path $scripts "acceptance.py") }
    "licence"   { & $py (Join-Path $scripts "choose_licence.py") }
    "license"   { & $py (Join-Path $scripts "choose_licence.py") }
    "update"    { & $py (Join-Path $scripts "update.py") }
    "hotkeys"   { & $py $installer }
    "uninstall" { & $py $installer --remove }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host "Use: start | restart | stop | status | text | telegram | check | why"
        Write-Host "     todo | can | ready | disk | vault | hands | voice"
        Write-Host "     rehearse | licence | setup | update | hotkeys | uninstall"
        Write-Host ""
        Write-Host "Not sure where to start?  .\jalen.ps1 todo" -ForegroundColor Cyan
        exit 1
    }
}
