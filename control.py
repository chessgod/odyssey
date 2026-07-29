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

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/getUpdates"
POLL_TIMEOUT_SECONDS = 30

HELP_TEXT = (
    "Commands:\n"
    "/status - uptime, checks, alerts, and failures per venue\n"
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

            kinds_this_check = {kind for _url, kind, _message in issues}
            for _url, kind, _message in issues:
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
                    f"{kind}: {message}" for _url, kind, message in issues
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


def perform_restart():
    """Exit the process. systemd (Restart=always) is expected to bring it back up."""
    logger.info("Restart requested via Telegram command, exiting")
    os._exit(0)


def handle_command(text: str, stats: Stats):
    command = text.strip().lower().split()[0] if text.strip() else ""
    if command in ("/status", "/stats"):
        notifier.send_message(stats.snapshot_text())
    elif command == "/restart":
        notifier.send_message("Restarting...")
        perform_restart()
    elif command in ("/help", "/start"):
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


def run_command_listener(stats: Stats):
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

            handle_command(text, stats)


def start_listener_thread(stats: Stats):
    thread = threading.Thread(target=run_command_listener, args=(stats,), daemon=True)
    thread.start()
    return thread
