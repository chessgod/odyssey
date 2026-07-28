"""Loop entrypoint: each watcher checks all its URLs, diffs, alerts."""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

import notifier

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "ticket_alerter.log")


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

    logger.info("Main watch loop not implemented yet (coming in a later step)")
    print("Main watch loop not implemented yet. Run with --test to check Telegram delivery.")


if __name__ == "__main__":
    main()
