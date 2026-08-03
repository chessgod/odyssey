"""Loop entrypoint: each watcher checks all its URLs, diffs, alerts."""

import argparse
import logging
import os
import random
import sys
import threading
import time

from dotenv import load_dotenv

import config
import control
import notifier
import state as state_module
from watchers.base import decoy_browse, diff
from watchers.bfi import BFIWatcher
from watchers.science_museum import ScienceMuseumWatcher

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "ticket_alerter.log")

MAX_BACKOFF_MULTIPLIER = 4

# How long a venue can go without a single successful check before we send
# one "still down, might be worth a manual look" alert instead of staying
# silent through the whole outage (Science Museum went 37h dark with zero
# visibility on 2026-08-01/02).
ESCALATION_THRESHOLD_SECONDS = 2 * 60 * 60


def _log_uncaught_exception(exc_type, exc_value, exc_traceback):
    """Route uncaught main-thread exceptions into the normal log handlers
    instead of bare stderr - two process restarts in the first 2 days had no
    corresponding traceback in this log file because of exactly that."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger(__name__).critical(
        "Uncaught exception, process exiting", exc_info=(exc_type, exc_value, exc_traceback)
    )


def _log_uncaught_thread_exception(args):
    logging.getLogger(__name__).critical(
        "Uncaught exception in background thread %r",
        args.thread.name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


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
    sys.excepthook = _log_uncaught_exception
    threading.excepthook = _log_uncaught_thread_exception


def build_watchers():
    return [
        BFIWatcher(config.bfi_urls(), config.BFI_DECOY_URLS),
        ScienceMuseumWatcher(config.science_museum_urls(), config.SCIENCE_MUSEUM_DECOY_URLS),
    ]


def maybe_decoy_browse(watchers, logger) -> float:
    """Sometimes (not every cycle - see config.DECOY_BROWSE_PROBABILITY),
    spend part of the idle time between checks visiting a generic page on
    one venue's site in a disposable browser session, so this IP's traffic
    looks like a visitor poking around rather than a script that only ever
    hits one exact URL on a fixed schedule. Returns seconds spent, so the
    caller can subtract it from the normal sleep instead of stacking on top."""
    candidates = [w for w in watchers if w.decoy_urls]
    if not candidates or random.random() >= config.DECOY_BROWSE_PROBABILITY:
        return 0.0
    watcher = random.choice(candidates)
    url = random.choice(watcher.decoy_urls)
    logger.info("%s: decoy browse starting (%s)", watcher.name, url)
    started = time.time()
    decoy_browse(url)
    return time.time() - started


def _migrate_venue_state(value, urls):
    """Two migrations in one, both idempotent - safe to run on every load
    regardless of which format the state is already in:

    1. Old flat {item_id: info} format (pre session-3) -> wrap as
       {"items": ..., "seen_urls": [...]}.
    2. seen_urls keyed by literal URL string (session-3's original scheme)
       -> re-keyed by window *index* instead. BFI/Science Museum's URLs now
       embed today's date and change daily (see config.bfi_urls), so a
       literal URL is never the same twice - left as URL-keyed, this would
       silently disable diffing entirely, since every window would look
       "never seen before" on every single day.

    Both migrations mark every currently configured window as already seen,
    on the same reasoning as the original migration: if state got this far,
    prior cycles already merged in whatever each window had to offer.
    """
    all_indices = list(range(len(urls)))
    if not (isinstance(value, dict) and "items" in value and "seen_urls" in value):
        return {"items": value, "seen_urls": all_indices}
    seen = value["seen_urls"]
    if seen and not all(isinstance(s, int) for s in seen):
        return {"items": value["items"], "seen_urls": all_indices}
    return value


def _maybe_send_escalation(stats, watcher):
    if stats.check_escalation(watcher.name, ESCALATION_THRESHOLD_SECONDS):
        hours = ESCALATION_THRESHOLD_SECONDS // 3600
        notifier.send_message(
            f"[ticket-alerter] {watcher.display_name} has had no successful check in "
            f"over {hours}h — likely still blocked. Try /peek {watcher.display_name} "
            "to check manually."
        )


def run_cycle(watchers, state, logger, stats) -> bool:
    """Run one check cycle for all watchers. Returns True if any fetch was blocked."""
    any_blocked = False

    for watcher in watchers:
        stats.ensure_venue(watcher.name, watcher.display_name)
        try:
            items_by_url, issues = watcher.check()
        except Exception:
            logger.exception("%s: unexpected error during check, skipping this cycle", watcher.name)
            continue

        for url, kind, message, screenshot in issues:
            if kind == "blocked":
                any_blocked = True
            elif kind == "parse_broken":
                notifier.send_message(
                    f"[ticket-alerter] {watcher.name} parser may be broken for {url}\n{message}"
                )
                if screenshot:
                    notifier.send_photo(
                        screenshot, caption=f"{watcher.display_name} — screenshot at time of failure"
                    )

        combined = {}
        for items in items_by_url.values():
            combined.update(items)

        venue_state = state.get(watcher.name)
        if venue_state is not None:
            venue_state = _migrate_venue_state(venue_state, watcher.urls)

        if venue_state is None:
            if not items_by_url and issues:
                logger.warning(
                    "%s: no items fetched this cycle (all URLs failed), deferring baseline",
                    watcher.name,
                )
                stats.record_check(watcher.name, len(watcher.urls), 0, 0, issues)
                _maybe_send_escalation(stats, watcher)
                continue
            state[watcher.name] = {"items": combined, "seen_urls": sorted(items_by_url.keys())}
            logger.info(
                "%s: baseline recorded (%d items, %d/%d URLs)",
                watcher.name,
                len(combined),
                len(items_by_url),
                len(watcher.urls),
            )
            stats.record_check(watcher.name, len(watcher.urls), len(combined), 0, issues)
            continue

        old_items = venue_state["items"]
        old_seen = set(venue_state["seen_urls"])

        # Only diff URLs that have contributed a baseline before. A URL
        # succeeding for the first time produces items that are new to *our
        # records*, not necessarily new tickets - diffing those against a
        # baseline that never had them is what caused the ~128 false "NEW"
        # alert flood on 2026-08-01 (4 of 5 BFI date windows finally
        # succeeding ~24h after the other one had already been baselined).
        # Such URLs are merged into state silently instead, same as any
        # other deferred baseline.
        diffable = {}
        newly_seen = []
        for idx, items in items_by_url.items():
            if idx in old_seen:
                diffable.update(items)
            else:
                newly_seen.append(idx)
        if newly_seen:
            logger.info(
                "%s: %d window(s) succeeded for the first time, merging without diffing: %s",
                watcher.name,
                len(newly_seen),
                [watcher.urls[i] for i in newly_seen],
            )

        alerts = diff(old_items, diffable)
        for msg in alerts:
            notifier.send_message(msg)
            logger.info("%s: alert sent: %s", watcher.name, msg.splitlines()[0])

        # Only overwrite items for URLs we actually managed to fetch this
        # cycle; keep prior entries for any URL that failed entirely.
        merged_items = dict(old_items)
        for items in items_by_url.values():
            merged_items.update(items)
        state[watcher.name] = {
            "items": merged_items,
            "seen_urls": sorted(old_seen | set(items_by_url.keys())),
        }

        stats.record_check(watcher.name, len(watcher.urls), len(merged_items), len(alerts), issues)
        _maybe_send_escalation(stats, watcher)

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

    stats = control.Stats()
    run_control = control.RunControl()
    control.start_listener_thread(stats, watchers, run_control)

    while True:
        run_control.wait_until_running()
        any_blocked = run_cycle(watchers, state, logger, stats)
        stats.record_cycle()
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

        decoy_elapsed = maybe_decoy_browse(watchers, logger)
        sleep_seconds = max(0, sleep_seconds - decoy_elapsed)

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
