"""Two-way Telegram control: /status and /restart commands.

A background thread long-polls Telegram getUpdates and reacts to commands
from the configured TELEGRAM_CHAT_ID only. Stats is a thread-safe counter
object the watch loop updates each cycle and /status reads from.
"""

import logging
import os
import threading
import time

import requests

import notifier
from watchers.base import BlockedError, FetchError, ParseError, fetch_rendered_html

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/getUpdates"
POLL_TIMEOUT_SECONDS = 30

HELP_TEXT = (
    "Commands:\n"
    "/status - uptime, checks, alerts, and failures per venue\n"
    "/peek [venue] - live fetch right now (text + screenshot) so you can\n"
    "  verify, e.g. /peek BFI or /peek Science Museum. No venue = all.\n"
    "/stop - pause checks (process keeps running so /start still works)\n"
    "/start - resume checks after /stop\n"
    "/restart - restart the process (systemd brings it back up)\n"
    "/help - show this message"
)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class Stats:
    """Thread-safe in-memory counters, reset on every process (re)start."""

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()
        self.cycles_completed = 0
        self.venues = {}

    def ensure_venue(self, name: str, display_name: str = None):
        with self._lock:
            self.venues.setdefault(
                name,
                {
                    "display_name": display_name or name,
                    "checks": 0,
                    "requests_made": 0,
                    "items_tracked": 0,
                    "alerts_sent": 0,
                    "fetch_failures": 0,
                    "blocked": 0,
                    "parse_errors": 0,
                    "last_check_time": None,
                    # Outcome of the most recent check specifically, so
                    # /status can distinguish "currently broken" from
                    # "broke once a while ago and has since recovered".
                    "last_check_outcome": "never",
                    "consecutive_issues": 0,
                    "last_success_time": None,
                    "last_issue_time": None,
                    "last_issue_message": None,
                },
            )

    def record_check(self, name: str, requests_made: int, items_tracked: int, alerts_sent: int, issues):
        now = time.time()
        with self._lock:
            v = self.venues[name]
            v["checks"] += 1
            v["requests_made"] += requests_made
            v["items_tracked"] = items_tracked
            v["alerts_sent"] += alerts_sent
            v["last_check_time"] = now

            kinds_this_check = {kind for _url, kind, _message, _screenshot in issues}
            for _url, kind, _message, _screenshot in issues:
                if kind == "blocked":
                    v["blocked"] += 1
                elif kind == "fetch_failed":
                    v["fetch_failures"] += 1
                elif kind == "parse_broken":
                    v["parse_errors"] += 1

            if kinds_this_check:
                v["last_check_outcome"] = ", ".join(sorted(kinds_this_check))
                v["consecutive_issues"] += 1
                v["last_issue_time"] = now
                v["last_issue_message"] = "; ".join(
                    f"{kind}: {message}" for _url, kind, message, _screenshot in issues
                )
            else:
                v["last_check_outcome"] = "ok"
                v["consecutive_issues"] = 0
                v["last_success_time"] = now

    def record_cycle(self):
        with self._lock:
            self.cycles_completed += 1

    def snapshot_text(self) -> str:
        with self._lock:
            now = time.time()
            uptime = format_duration(now - self.start_time)
            lines = [f"Uptime: {uptime} | Cycles completed: {self.cycles_completed}"]

            for name, v in self.venues.items():
                lines.append(f"\n== {v['display_name']} ==")

                if v["last_check_time"] is None:
                    lines.append("Status: not checked yet")
                    continue

                outcome = v["last_check_outcome"]
                if outcome == "ok":
                    lines.append("Status: OK")
                else:
                    since = (
                        format_duration(now - v["last_issue_time"])
                        if v["last_issue_time"]
                        else "?"
                    )
                    streak = v["consecutive_issues"]
                    plural = "check" if streak == 1 else "checks"
                    lines.append(
                        f"Status: CURRENTLY {outcome.upper()} "
                        f"({streak} {plural} in a row, started {since} ago)"
                    )

                items_line = f"Items tracked: {v['items_tracked']}"
                if outcome != "ok" and v["last_success_time"]:
                    items_line += f" (as of last success, {format_duration(now - v['last_success_time'])} ago)"
                lines.append(items_line)
                lines.append(f"Last checked: {format_duration(now - v['last_check_time'])} ago")

                lines.append(
                    f"• Checks: {v['checks']}\n"
                    f"• Requests: {v['requests_made']}\n"
                    f"• Alerts sent: {v['alerts_sent']}\n"
                    f"• Fetch failures: {v['fetch_failures']}\n"
                    f"• Blocked: {v['blocked']}\n"
                    f"• Parse errors: {v['parse_errors']}"
                )

                if outcome != "ok":
                    lines.append(f"Last issue: {v['last_issue_message']}")
                elif v["last_issue_time"]:
                    lines.append(
                        f"Last issue: resolved, occurred {format_duration(now - v['last_issue_time'])} ago"
                    )

            return "\n".join(lines)


class RunControl:
    """Pauses/resumes the watch loop in-process via /stop and /start, without
    killing the process - so the command listener (and /start) stay reachable
    even while paused. No systemd/sudo involved, no privilege escalation."""

    def __init__(self):
        self._running = threading.Event()
        self._running.set()
        self.stopped_at = None

    def stop(self):
        self._running.clear()
        self.stopped_at = time.time()

    def start(self):
        self._running.set()
        self.stopped_at = None

    def is_running(self) -> bool:
        return self._running.is_set()

    def wait_until_running(self):
        self._running.wait()


def perform_restart():
    """Exit the process. systemd (Restart=always) is expected to bring it back up."""
    logger.info("Restart requested via Telegram command, exiting")
    os._exit(0)


def handle_peek(watchers, venue_query: str = None):
    """Fetch venues live right now (bypassing the schedule) and reply with the
    actual parsed data plus a screenshot, so it can be checked against the
    real site directly rather than trusted blind. With no venue_query, peeks
    every venue; otherwise only venues whose display name matches (case
    insensitive, substring)."""
    if venue_query:
        query = venue_query.strip().lower()
        matched = [w for w in watchers if query in w.display_name.lower()]
        if not matched:
            known = ", ".join(w.display_name for w in watchers)
            notifier.send_message(f"No venue matching {venue_query!r}. Known venues: {known}")
            return
        watchers = matched

    for watcher in watchers:
        for url in watcher.urls:
            try:
                html, screenshot = fetch_rendered_html(url, capture_screenshot=True)
            except BlockedError as e:
                notifier.send_message(f"{watcher.display_name}: blocked right now\n{e}\n{url}")
                if e.screenshot:
                    notifier.send_photo(
                        e.screenshot, caption=f"{watcher.display_name} — live screenshot (blocked)"
                    )
                continue
            except FetchError as e:
                notifier.send_message(f"{watcher.display_name}: fetch failed\n{e}\n{url}")
                if e.screenshot:
                    notifier.send_photo(
                        e.screenshot, caption=f"{watcher.display_name} — live screenshot (fetch failed)"
                    )
                continue

            try:
                items = watcher.parse_url(url, html)
            except ParseError as e:
                notifier.send_message(f"{watcher.display_name}: parser broke\n{e}\n{url}")
                if screenshot:
                    notifier.send_photo(
                        screenshot, caption=f"{watcher.display_name} — live screenshot (parse failed)"
                    )
                continue

            if items:
                lines = [f"{watcher.display_name} — live peek, {len(items)} items", url, ""]
                for item_id, info in sorted(items.items()):
                    lines.append(f"{item_id}: {info['status']}")
                notifier.send_message("\n".join(lines))
            else:
                notifier.send_message(f"{watcher.display_name}: 0 items right now\n{url}")

            if screenshot:
                notifier.send_photo(screenshot, caption=f"{watcher.display_name} — live screenshot")


def handle_command(text: str, stats: Stats, watchers, run_control: RunControl):
    stripped = text.strip()
    if not stripped:
        return
    parts = stripped.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else None

    if command in ("/status", "/stats"):
        if run_control.is_running():
            header = "Watch loop: RUNNING"
        else:
            since = format_duration(time.time() - run_control.stopped_at)
            header = f"Watch loop: STOPPED ({since} ago, send /start to resume)"
        notifier.send_message(header + "\n\n" + stats.snapshot_text())
    elif command == "/peek":
        handle_peek(watchers, arg)
    elif command == "/stop":
        if run_control.is_running():
            run_control.stop()
            notifier.send_message("Stopped. Checks paused — send /start to resume.")
        else:
            notifier.send_message("Already stopped.")
    elif command == "/start":
        if run_control.is_running():
            notifier.send_message("Already running.")
        else:
            run_control.start()
            notifier.send_message("Resumed. Checks will continue on schedule.")
    elif command == "/restart":
        notifier.send_message("Restarting...")
        perform_restart()
    elif command == "/help":
        notifier.send_message(HELP_TEXT)
    else:
        logger.info("Command listener: unrecognized command %r", text)


def _clear_backlog(url: str) -> int:
    """Discard any pending updates from before startup, return the next offset."""
    try:
        resp = requests.get(url, params={"timeout": 0}, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("result", [])
        if results:
            return results[-1]["update_id"] + 1
    except requests.RequestException:
        logger.warning("Command listener: failed to clear backlog, continuing anyway")
    return None


def run_command_listener(stats: Stats, watchers, run_control: RunControl):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("Command listener not started: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return

    url = TELEGRAM_API_URL.format(token=token)
    offset = _clear_backlog(url)
    logger.info("Command listener started")

    while True:
        try:
            params = {"timeout": POLL_TIMEOUT_SECONDS}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(url, params=params, timeout=POLL_TIMEOUT_SECONDS + 10)
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except requests.RequestException:
            logger.warning("Command listener: failed to poll Telegram, retrying shortly")
            time.sleep(10)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            text = message.get("text") or ""
            sender_chat_id = str(message.get("chat", {}).get("id", ""))

            if sender_chat_id != str(chat_id):
                logger.warning(
                    "Command listener: ignoring message from unauthorized chat %s", sender_chat_id
                )
                continue

            handle_command(text, stats, watchers, run_control)


def start_listener_thread(stats: Stats, watchers, run_control: RunControl):
    thread = threading.Thread(
        target=run_command_listener, args=(stats, watchers, run_control), daemon=True
    )
    thread.start()
    return thread
