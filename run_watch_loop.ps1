# Auto-restart supervisor for main.py, replacing systemd's Restart=always
# for a Windows deployment. Run this from a PowerShell window (it needs to
# stay open) rather than running main.py directly.
#
# One-time setup before first run:
#   pip install -r requirements.txt
#   playwright install chromium
#   copy .env.example to .env and fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
#
# Also set the laptop's power plan to never sleep and to stay on when the
# lid is closed (if applicable) - a suspend mid-run kills the process the
# same as any other crash, just silently, with nothing left to restart it.
#
# Ctrl+C stops this whole script (and the running main.py with it) - that's
# the intended way to stop everything. Any other exit (crash, or /restart
# via Telegram) is followed by a 5s pause and a fresh restart, same as
# systemd's Restart=always.

Set-Location -Path $PSScriptRoot

while ($true) {
    Write-Host "$(Get-Date -Format o) Starting ticket-alerter..."
    python main.py
    $exitCode = $LASTEXITCODE
    Write-Host "$(Get-Date -Format o) ticket-alerter exited (code $exitCode). Restarting in 5s... (Ctrl+C to stop)"
    Start-Sleep -Seconds 5
}
