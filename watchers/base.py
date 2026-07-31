"""Shared fetch + diff logic for all watchers."""

import logging
import time

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Both target sites are UK venues; a browser context that actually looks like
# an ordinary UK desktop visitor (locale, timezone, viewport, Accept-Language)
# measurably avoids bot-protection blocks that Playwright's bare defaults
# (no Accept-Language header, generic viewport) tend to trip. This is just
# presenting an honest, realistic browser fingerprint - not solving or
# bypassing any human-verification challenge.
BROWSER_LOCALE = "en-GB"
BROWSER_TIMEZONE = "Europe/London"
BROWSER_VIEWPORT = {"width": 1366, "height": 768}
EXTRA_HTTP_HEADERS = {"Accept-Language": "en-GB,en;q=0.9"}

UNAVAILABLE_STATUSES = {"sold_out"}

BLOCK_TITLE_MARKERS = ("just a moment", "attention required", "access denied")
BLOCK_CONTENT_MARKERS = ("incapsula incident", "request unsuccessful")

# Both target sites use the same OneTrust cookie consent banner, which
# visually covers page content (and screenshots) until dismissed.
COOKIE_ACCEPT_SELECTOR = "#onetrust-accept-btn-handler"


class FetchError(Exception):
    """A URL could not be fetched this cycle. Non-fatal, cycle continues."""

    def __init__(self, message, screenshot=None):
        super().__init__(message)
        self.screenshot = screenshot


class BlockedError(FetchError):
    """Fetch was blocked by rate limiting, a bot-protection challenge, or a
    queue/waiting-room page. May well be transient."""


class ParseError(Exception):
    """A page's structure didn't match what the parser expected. May be a
    genuinely broken parser, or may be a transient interstitial (queue page,
    slow challenge) that just doesn't look like a block either - both get
    retried the same way before this is treated as worth alerting on."""


def fetch_rendered_html(
    url: str, wait_ms: int = 6000, timeout_ms: int = 30000, capture_screenshot: bool = False
):
    """Fetch a URL with a real browser, return (html, screenshot_bytes_or_None).

    The screenshot is captured before any block check, so it's available on
    BlockedError/FetchError too (via the exception's .screenshot attribute) -
    useful for seeing what an unrecognized interstitial actually looks like.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale=BROWSER_LOCALE,
                timezone_id=BROWSER_TIMEZONE,
                viewport=BROWSER_VIEWPORT,
                extra_http_headers=EXTRA_HTTP_HEADERS,
            )
            page = context.new_page()
            response = page.goto(url, timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)

            try:
                page.locator(COOKIE_ACCEPT_SELECTOR).click(timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                pass  # no cookie banner present, or it didn't appear in time

            status = response.status if response else None
            title = (page.title() or "").strip().lower()
            content = page.content()
            lower_content = content.lower()
            screenshot = page.screenshot(full_page=True) if capture_screenshot else None

            if status == 429:
                raise BlockedError(f"Rate limited (429) fetching {url}", screenshot=screenshot)
            if any(marker in title for marker in BLOCK_TITLE_MARKERS):
                raise BlockedError(
                    f"Blocked by bot-protection challenge fetching {url} (title={title!r})",
                    screenshot=screenshot,
                )
            if any(marker in lower_content for marker in BLOCK_CONTENT_MARKERS):
                raise BlockedError(
                    f"Blocked by bot-protection challenge fetching {url}", screenshot=screenshot
                )
            if status is not None and status >= 400:
                raise FetchError(f"HTTP {status} fetching {url}", screenshot=screenshot)

            return content, screenshot
        finally:
            browser.close()


RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 15


class Watcher:
    """Base class. Subclasses implement parse_url() for one venue's page structure."""

    name = "base"
    display_name = "Base"

    def __init__(self, urls):
        self.urls = urls

    def parse_url(self, url: str, html: str) -> dict:
        """Return {item_id: {"status": str, "label": str, "url": str}} for one page."""
        raise NotImplementedError

    def check(self):
        """Fetch + parse all URLs, merged into one combined item-set for this venue.

        Each URL gets up to RETRY_ATTEMPTS tries, spaced RETRY_WAIT_SECONDS
        apart, before giving up - a queue page, a slow-clearing challenge, or
        any other transient interstitial gets a real chance to resolve within
        the same cycle instead of immediately being treated as a failure.

        Returns (combined_items, issues) where issues is a list of
        (url, kind, message, screenshot_or_None) with kind in
        {"fetch_failed", "blocked", "parse_broken"}. One URL failing does not
        stop the others from being checked.
        """
        combined = {}
        issues = []
        for url in self.urls:
            outcome = None  # (kind, message, screenshot)
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                is_last_attempt = attempt == RETRY_ATTEMPTS
                html = None
                screenshot = None

                try:
                    html, screenshot = fetch_rendered_html(url, capture_screenshot=is_last_attempt)
                except BlockedError as e:
                    outcome = ("blocked", str(e), e.screenshot)
                except FetchError as e:
                    outcome = ("fetch_failed", str(e), e.screenshot)

                if html is not None:
                    try:
                        items = self.parse_url(url, html)
                        combined.update(items)
                        outcome = None
                        break
                    except ParseError as e:
                        outcome = ("parse_broken", str(e), screenshot)

                if not is_last_attempt:
                    logger.info(
                        "%s: attempt %d/%d failed for %s (%s), retrying in %ds",
                        self.name,
                        attempt,
                        RETRY_ATTEMPTS,
                        url,
                        outcome[0] if outcome else "?",
                        RETRY_WAIT_SECONDS,
                    )
                    time.sleep(RETRY_WAIT_SECONDS)

            if outcome is not None:
                kind, message, screenshot = outcome
                issues.append((url, kind, message, screenshot))
                logger.warning("%s: %s for %s: %s", self.name, kind, url, message)

        return combined, issues


def diff(old: dict, new: dict) -> list:
    """Compare snapshots, return alert message strings for new/back-in-stock items."""
    alerts = []
    for item_id, info in new.items():
        old_info = old.get(item_id)
        if old_info is None:
            alerts.append(f"NEW: {info['label']}\n{info['url']}")
        elif (
            old_info.get("status") in UNAVAILABLE_STATUSES
            and info.get("status") not in UNAVAILABLE_STATUSES
        ):
            alerts.append(f"BACK IN STOCK: {info['label']}\n{info['url']}")
    return alerts
