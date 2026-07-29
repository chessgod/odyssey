"""Loop entrypoint: each watcher checks all its URLs, diffs, alerts."""

import argparse
import logging
import os
import random
import sys
import time

from dotenv import load_dotenv

import config
import notifier
import state as state_module
from watchers.base import diff
from watchers.bfi import BFIWatcher
from watchers.science_museum import ScienceMuseumWatcher

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "ticket_alerter.log")

MAX_BACKOFF_MULTIPLIER = 8


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout),
        ],
    )


def build_watchers():
    return [
        BFIWatcher(config.BFI_URLS),
        ScienceMuseumWatcher(config.SCIENCE_MUSEUM_URLS),
    ]


def run_cycle(watchers, state, logger) -> bool:
    """Run one check cycle for all watchers. Returns True if any fetch was blocked."""
    any_blocked = False

    for watcher in watchers:
        try:
            combined, issues = watcher.check()
        except Exception:
            logger.exception("%s: unexpected error during check, skipping this cycle", watcher.name)
            continue

        for url, kind, message in issues:
            if kind == "blocked":
                any_blocked = True
            elif kind == "parse_broken":
                notifier.send_message(
                    f"[ticket-alerter] {watcher.name} parser may be broken for {url}\n{message}"
                )

        old = state.get(watcher.name)
        if old is None:
            if not combined and issues:
                logger.warning(
                    "%s: no items fetched this cycle (all URLs failed), deferring baseline",
                    watcher.name,
                )
                continue
            state[watcher.name] = combined
            logger.info("%s: baseline recorded (%d items)", watcher.name, len(combined))
            continue

        alerts = diff(old, combined)
        for msg in alerts:
            notifier.send_message(msg)
            logger.info("%s: alert sent: %s", watcher.name, msg.splitlines()[0])

        # Only overwrite state with items we actually managed to fetch this
        # cycle; keep prior entries for any URL that failed entirely.
        if combined:
            merged = dict(old)
            merged.update(combined)
            state[watcher.name] = merged

        logger.info(
            "%s: checked (%d items, %d alerts, %d issues)",
            watcher.name,
            len(combined),
            len(alerts),
            len(issues),
        )

    return any_blocked


def watch_loop(logger):
    watchers = build_watchers()
    state = state_module.load()
    consecutive_blocked_cycles = 0

    while True:
        any_blocked = run_cycle(watchers, state, logger)
        state_module.save(state)

        if any_blocked:
            consecutive_blocked_cycles += 1
        else:
            consecutive_blocked_cycles = 0

        backoff_multiplier = min(2**consecutive_blocked_cycles, MAX_BACKOFF_MULTIPLIER)
        sleep_seconds = config.CHECK_INTERVAL_SECONDS + random.uniform(
            -config.JITTER_SECONDS, config.JITTER_SECONDS
        )
        sleep_seconds = max(10, sleep_seconds) * backoff_multiplier

        logger.info("Cycle complete. Sleeping %.0fs (backoff x%d)", sleep_seconds, backoff_multiplier)
        time.sleep(sleep_seconds)


def main():
    parser = argparse.ArgumentParser(description="24/7 ticket-drop alerter")
    parser.add_argument(
        "--test", action="store_true", help="Send a single test Telegram message and exit"
    )
    args = parser.parse_args()

    load_dotenv()
    setup_logging()
    logger = logging.getLogger(__name__)

    if args.test:
        ok = notifier.send_message(
            "Ticket alerter: test message. If you see this, Telegram delivery works."
        )
        if ok:
            logger.info("Test message sent successfully")
            print("Test message sent. Check Telegram.")
        else:
            logger.error("Test message failed to send")
            print("Failed to send test message. Check logs/ticket_alerter.log")
            sys.exit(1)
        return

    logger.info("Starting ticket-drop alerter watch loop")
    try:
        watch_loop(logger)
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
