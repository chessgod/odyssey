# Ticket-Drop Alerter

This has been built entirely by Claude Code. I supervised it and fed it a lengthy and concise prompt. 

Polls a set of ticket pages every ~3 minutes (with jitter) and sends a Telegram
message when a new item appears or an existing item flips from sold out to
available. **Alert-only** — it never buys, reserves, adds to basket, submits
forms, or logs in.

Currently watches:
- **BFI Odyssey IMAX 70mm** — availability across the full run (currently
  ~130 performances, July 2026 through the last known December dates), via
  a handful of date-range-filtered search URLs.
- **Science Museum — The Odyssey** — per-performance availability from the events calendar.

## How it works

- `watchers/base.py` — shared Playwright fetch + diff logic, plus bot-protection detection.
- `watchers/bfi.py`, `watchers/science_museum.py` — per-site parsers.
- `state.py` — persists the last-seen snapshot to `data/state.json` so restarts don't re-alert.
- `notifier.py` — sends Telegram messages.
- `control.py` — background thread that listens for `/status`, `/peek`,
  `/stop`, `/start`, and `/restart` commands sent back to the bot, and the
  in-memory stats counters `/status` reads from.
- `config.py` — URLs, poll interval, jitter (no secrets).
- `main.py` — the loop.

Both sites are JavaScript-rendered, so both are fetched with Playwright
(headless Chromium), not plain `requests`.

## Remote control via Telegram

While the watch loop is running, message the bot directly:

- `/status` (or `/stats`) — uptime, cycles completed, and per-venue checks,
  requests made, alerts sent, fetch failures, blocks, and parse errors.
  Distinguishes a problem that's happening right now from one that's
  already resolved (e.g. `Status: CURRENTLY BLOCKED (3 checks in a row,
  started 12m ago)` vs `Status: OK` with `Last issue: resolved, occurred
  12m ago`).
- `/peek [venue]` — fetches live right now (bypassing the schedule) and
  replies with the actual parsed data plus a screenshot of the real
  rendered page, so you can check it against the live site yourself instead
  of trusting the alerter blind. With no venue, peeks all of them; name one
  to scope it, e.g. `/peek BFI` or `/peek Science Museum` (case-insensitive,
  matches by substring against the venue's display name).
- `/stop` — pauses checks. The process keeps running (so `/start` still
  works) — it's an in-process pause via a shared flag, not a real
  `systemctl stop`. No sudo, no shelling out, same safety posture as
  everything else here. Doesn't persist across a restart/crash/reboot —
  those always come back up running by default.
- `/start` — resumes checks after `/stop`.
- `/restart` — exits the process. Under systemd with `Restart=always` (see
  below), it comes straight back up — handy for restarting from your phone.
- `/help` — lists the commands.

Only messages from the `TELEGRAM_CHAT_ID` in your `.env` are accepted;
anything else is logged and ignored. Stats are in-memory and reset on every
restart (including `/restart` itself) — they reflect the current run, not
a historical total.

### Known quirks (found by inspecting the real pages)

- **BFI**: clicking pagination or a date in the on-page calendar both go
  through a search backend gated by an interactive Cloudflare Turnstile
  challenge (an actual "verify you're human" checkbox) that doesn't clear
  on its own when reached via a same-session click/AJAX navigation.
  Turnstile isn't something this project will try to bypass. What does
  work: a **fresh top-level navigation** straight to a date-range-filtered
  search URL (never clicked-into from another page in the same browser
  session) loads cleanly, same as loading page 1 directly does — Turnstile
  appears to trigger on in-session navigation specifically, not on the
  search endpoint itself. `config.BFI_URLS` is a handful of these
  constructed URLs, each covering a date window sized to stay under the
  50-result-per-page limit (the parser raises an alert if a window ever
  fills up, rather than silently truncating). Since a date-range search
  returns every film playing then, not just this one, results are filtered
  per-item by name rather than trusting the whole page. An earlier version
  of the parser instead read a `calendar_days` JSON block embedded in the
  page, assuming its numeric codes meant per-date availability; that
  assumption turned out to be wrong (it reported dates as available that
  were actually sold out, unverifiable against anything real), so it was
  dropped for this actually-checkable approach.
- **Science Museum**: sits behind Incapsula. A realistic browser context
  (locale, timezone, viewport, `Accept-Language` — see below) measurably
  helps versus Playwright's bare defaults, but real production logs showed
  it still failing on a fresh VM IP that had never touched the site before,
  almost the entire first day. That points less at bot-fingerprinting and
  more at genuine high-demand queuing/rate-limiting affecting everyone,
  which matches what's visible manually on the site. See "Retries and
  backoff" below for how this is handled.
- **Identity checks**: each parser verifies the fetched page/items actually
  mention the expected film (`config.BFI_EXPECTED_KEYWORD` /
  `SCIENCE_MUSEUM_EXPECTED_KEYWORD`) before trusting the data. A mismatch is
  treated as a broken parser (alerts you) instead of silently trusting
  whatever happened to be on the page.
- **Cookie banner**: both sites show the same OneTrust cookie consent
  banner, which visually covers page content until dismissed. Every fetch
  clicks it away (best-effort, harmless if absent) so `/peek` screenshots
  show the real page, not a banner.
- If a venue's very first check ever fails outright (e.g. blocked), the
  baseline is deferred to the next successful check — so you won't get a
  flood of false "NEW" alerts once the site becomes reachable again.

### Retries and backoff

Each URL gets up to 3 attempts, 15 seconds apart, before a check is treated
as failed for that cycle (`watchers/base.py`, `RETRY_ATTEMPTS` /
`RETRY_WAIT_SECONDS`). This covers a queue page, a slow-clearing Cloudflare
challenge, or any other short-lived interstitial — instead of immediately
giving up and letting the much longer inter-cycle backoff kick in, which
would risk sleeping through the exact moment a drop happens (ticket drops
cause traffic spikes, traffic spikes cause queues/blocks — backing off
harder in response is backwards for an alerter). The final attempt for a
URL always grabs a screenshot, which gets attached to the "parser may be
broken" Telegram alert automatically — so a queue page or other unexpected
interstitial is visible without needing to catch it live with `/peek`.

On top of that, if a whole cycle comes back blocked, the *next* cycle's
wait is multiplied (2x, capped at 4x the normal interval) before trying
again — deliberately capped low (~12–16 min max, not the nearly-an-hour it
used to allow) for the same reason: the in-cycle retries already absorb
short blips, so inter-cycle backoff is really just for genuinely sustained
outages, where being that much more polite isn't worth the size of the
blind spot it creates.

With BFI now spanning several URLs, a fully-bad cycle (everything retried
to exhaustion) can take several minutes rather than seconds — the loop just
runs the next cycle later than usual when that happens, nothing breaks.

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
2. Open port 22 only. This still holds even with `/status`, `/peek`, and
   `/restart`: the command listener works by long-polling Telegram's
   `getUpdates` API — the VM repeatedly asks Telegram "any new messages?"
   over an outbound HTTPS connection, rather than Telegram pushing to a
   webhook on the VM. Nothing in this app binds a socket or listens for
   inbound connections; everything (alerts, screenshots, and commands) is
   outbound-only. Leave the default security list as-is or lock it down
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
