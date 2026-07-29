# Ticket-Drop Alerter

This has been built entirely by Claude Code. I supervised it and fed it a lengthy and concise prompt. 

Polls a set of ticket pages every ~3 minutes (with jitter) and sends a Telegram
message when a new item appears or an existing item flips from sold out to
available. **Alert-only** — it never buys, reserves, adds to basket, submits
forms, or logs in.

Currently watches:
- **BFI Odyssey IMAX 70mm** — per-date availability from the film's permalink page.
- **Science Museum — The Odyssey** — per-performance availability from the events calendar.

## How it works

- `watchers/base.py` — shared Playwright fetch + diff logic, plus bot-protection detection.
- `watchers/bfi.py`, `watchers/science_museum.py` — per-site parsers.
- `state.py` — persists the last-seen snapshot to `data/state.json` so restarts don't re-alert.
- `notifier.py` — sends Telegram messages.
- `control.py` — background thread that listens for `/status` and `/restart`
  commands sent back to the bot, and the in-memory stats counters they read.
- `config.py` — URLs, poll interval, jitter (no secrets).
- `main.py` — the loop.

Both sites are JavaScript-rendered, so both are fetched with Playwright
(headless Chromium), not plain `requests`.

## Remote control via Telegram

While the watch loop is running, message the bot directly:

- `/status` (or `/stats`) — uptime, cycles completed, and per-venue checks,
  requests made, alerts sent, fetch failures, blocks, and parse errors.
- `/restart` — exits the process. Under systemd with `Restart=always` (see
  below), it comes straight back up — handy for restarting from your phone.
- `/help` — lists the commands.

Only messages from the `TELEGRAM_CHAT_ID` in your `.env` are accepted;
anything else is logged and ignored. Stats are in-memory and reset on every
restart (including `/restart` itself) — they reflect the current run, not
a historical total.

### Known quirks (found by inspecting the real pages)

- **BFI**: only the film's permalink URL is watched. Its paginated search
  results (page 2, 3, ...) use a session-bound token and are gated by an
  interactive Cloudflare Turnstile challenge that doesn't clear automatically
  — so they're deliberately not scraped. Instead, the permalink page embeds a
  `calendar_days` JSON block with per-date availability for a rolling ~6-week
  window, which is what gets parsed. This means dates further out (e.g. a
  screening in December when today is July) won't be tracked until they roll
  into that window.
- **Science Museum**: uses Incapsula bot protection. It can intermittently
  block a fetch, especially under rapid repeated requests. The watcher
  detects this (`BlockedError`) and backs off rather than treating it as a
  parsing failure or a false "item removed" event.
- If a venue's very first check ever fails outright (e.g. blocked), the
  baseline is deferred to the next successful check — so you won't get a
  flood of false "NEW" alerts once the site becomes reachable again.

## 1. Telegram bot setup

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
   Copy the **bot token** it gives you.
2. Message your new bot anything (e.g. "hi") so it can message you back.
3. Get your **chat ID**: visit
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser after
   step 2, and read the `"chat":{"id": ...}` value from the JSON response.
   (Alternatively, message **@userinfobot** and it will reply with your ID.)

## 2. Environment variables

Copy the example file and fill in your values — never commit `.env`:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token
TELEGRAM_CHAT_ID=123456789
```

## 3. Install

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install --with-deps chromium
```

## 4. Run

Test Telegram delivery first:

```bash
python3 main.py --test
```

Then run the watch loop in the foreground:

```bash
python3 main.py
```

Logs go to `logs/ticket_alerter.log` (and stdout). State persists to
`data/state.json`. Both directories are created automatically and are
git-ignored.

## 5. Editing targets

Add or change URLs in `config.py` — each venue takes a **list** of URLs
(e.g. one per date range); a drop on any of them triggers an alert that
names the specific URL that changed. `CHECK_INTERVAL_SECONDS` and
`JITTER_SECONDS` control the poll cadence.

## 6. Running 24/7

### Option A: systemd (any always-on Linux box, incl. an Oracle Cloud VM)

Create `/etc/systemd/system/ticket-alerter.service` (adjust `User` and
paths for your setup):

```ini
[Unit]
Description=Ticket-drop alerter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/ticket-alerter
EnvironmentFile=/home/ubuntu/ticket-alerter/.env
ExecStart=/home/ubuntu/ticket-alerter/.venv/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Note: `EnvironmentFile` reads `KEY=value` lines directly, so `.env` works
as-is (no need for `python-dotenv` in that path, though `main.py` also
loads it itself, so both work).

`Restart=always` (not `on-failure`) is required for the `/restart` Telegram
command to actually bring the process back — `/restart` exits cleanly with
status 0, which `on-failure` would treat as "nothing to restart."

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ticket-alerter
sudo systemctl status ticket-alerter
journalctl -u ticket-alerter -f
```

### Option B: Oracle Cloud Always Free VM (end to end)

1. Create an **Always Free** compute instance (Ampere A1 or VM.Standard.E2.1.Micro,
   Ubuntu 22.04/24.04 image) in the OCI console.
2. Open port 22 only — this app makes outbound requests, it doesn't need any
   inbound ports open. Leave the default security list as-is or lock it down
   further; no need to open anything new.
3. SSH in and set up the system:

   ```bash
   sudo apt-get update
   sudo apt-get install -y python3-pip python3-venv git
   ```

4. Get the code onto the VM (e.g. `git clone` your repo, or `scp` it over),
   then:

   ```bash
   cd ticket-alerter
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python3 -m playwright install --with-deps chromium
   cp .env.example .env
   nano .env   # fill in your Telegram token + chat id
   python3 main.py --test   # confirm delivery works from the VM's IP
   ```

5. Install the systemd unit from Option A above, pointing `WorkingDirectory`
   and `ExecStart` at `/home/ubuntu/ticket-alerter` (or wherever you placed
   it), then enable it. The VM will keep the alerter running across reboots
   and restart it if it crashes.

6. Check in on it occasionally:

   ```bash
   journalctl -u ticket-alerter -f      # live logs
   tail -f ~/ticket-alerter/logs/ticket_alerter.log
   ```

**Note on IPs**: bot-protection services (Cloudflare, Incapsula) sometimes
treat known cloud/datacenter IP ranges — including Oracle Cloud's — with
more suspicion than home broadband IPs. If Science Museum checks start
showing `blocked` in the logs more often than expected, that's why; the
built-in backoff handles it, but persistent blocking is a real possibility
worth watching the logs for after deployment.
