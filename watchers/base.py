"""Shared fetch + diff logic for all watchers."""

import logging
import os
import random
import re
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
# "additional security check"/"protected and accelerated by imperva" catch
# Imperva's hCaptcha-backed challenge page for Science Museum, which has its
# own title/copy - previously unrecognized, so it fell through to parse_url()
# and got misreported as a broken parser instead of a block.
BLOCK_CONTENT_MARKERS = (
    "incapsula incident",
    "request unsuccessful",
    "additional security check",
    "protected and accelerated by imperva",
)

# Both target sites use the same OneTrust cookie consent banner, which
# visually covers page content (and screenshots) until dismissed.
COOKIE_ACCEPT_SELECTOR = "#onetrust-accept-btn-handler"

# Cloudflare's non-interactive Turnstile challenge often clears itself within
# a few extra seconds of JS execution in a real browser - so if the page
# still looks blocked after the normal wait, give it a few more short polls
# before giving up, instead of immediately declaring the attempt blocked.
# Only kicks in when a page looks blocked; clean pages are unaffected.
CHALLENGE_POLL_INTERVAL_MS = 3000
CHALLENGE_MAX_EXTRA_WAIT_MS = 12000


def _looks_blocked(title: str, lower_content: str) -> bool:
    return any(marker in title for marker in BLOCK_TITLE_MARKERS) or any(
        marker in lower_content for marker in BLOCK_CONTENT_MARKERS
    )


# Science Museum sometimes routes visitors through a Queue-it virtual waiting
# room before letting them through to the real page - identifiable by the
# browser being redirected to a queue-it.net URL. This is not a broken parser
# and not a bot-protection block; it clears on its own once your turn comes,
# so it's waited out within the same page/session rather than raising
# ParseError or retrying with a brand new session (which would just rejoin
# the queue from scratch and never get through).
QUEUE_IT_HOSTNAME_MARKER = "queue-it.net"
QUEUE_POLL_INTERVAL_MS = 10000
QUEUE_MAX_WAIT_MS = 5 * 60 * 1000
# If left idle too long, Queue-it's default UI shows a "you still there?"
# confirmation dialog ("Yes, please") that must be dismissed or it silently
# drops you from the queue. Matched case-insensitively since the button may
# render visually uppercase via CSS while the actual DOM text stays mixed
# case; whitespace after the comma is flexible in case of minor rendering
# differences, but the comma itself is required now that the exact text is
# confirmed.
QUEUE_STILL_THERE_TEXT_RE = re.compile(r"yes,\s*please", re.I)


def _in_queue_it(page) -> bool:
    return QUEUE_IT_HOSTNAME_MARKER in (page.url or "")


def _wait_out_queue_it(page, url):
    """If we've been redirected to a Queue-it waiting room, poll until it
    releases us back to the real site (or give up past QUEUE_MAX_WAIT_MS)."""
    if not _in_queue_it(page):
        return
    logger.info("%s: redirected to a Queue-it waiting room, waiting for it to clear", url)
    waited_ms = 0
    while _in_queue_it(page):
        try:
            page.get_by_text(QUEUE_STILL_THERE_TEXT_RE).click(timeout=1000)
            logger.info("%s: dismissed Queue-it 'still there?' prompt", url)
        except Exception:
            pass  # prompt isn't showing right now, nothing to do
        if waited_ms >= QUEUE_MAX_WAIT_MS:
            screenshot = page.screenshot(full_page=True)
            raise BlockedError(
                f"Still in Queue-it queue after {waited_ms // 1000}s fetching {url}",
                screenshot=screenshot,
            )
        page.wait_for_timeout(QUEUE_POLL_INTERVAL_MS)
        waited_ms += QUEUE_POLL_INTERVAL_MS
    logger.info("%s: Queue-it cleared after %ds", url, waited_ms // 1000)


# BFI's block is Cloudflare Turnstile in interactive checkbox mode ("Verify
# you are human"); Science Museum's is Imperva fronting an hCaptcha checkbox
# ("I am human" - Imperva's own copy says just clicking it is enough, no
# puzzle/image step). This clicks the checkbox once, the same single
# interaction a human visitor makes - not solving anything, no image/vision
# work. If the widget isn't present, isn't loaded yet, or the click doesn't
# actually clear the challenge, this is a no-op and the normal poll/wait/
# retry handles it exactly as if this had never run.
#
# Earlier version targeted specific iframe src patterns one level deep
# (iframe[src*='challenges.cloudflare.com'], iframe[src*='hcaptcha.com']) and
# didn't reliably find/click the checkbox in production. The exact DOM
# nesting for these widgets isn't guaranteed stable or knowable without live
# inspection, so instead of guessing a src pattern or nesting depth, this
# searches *every* frame on the page (page.frames flattens all nesting
# levels) for anything checkbox-shaped.
CAPTCHA_CHECKBOX_SELECTOR = "input[type=checkbox], #checkbox, [role=checkbox]"


def _human_like_move_and_click_point(page, target_x, target_y):
    """Move the mouse to (target_x, target_y) along a short, slightly wobbly
    multi-step path with small pauses, then press and release - rather than
    an instant teleport-click. A click with zero prior mouse movement is
    itself a behavioral bot signal to these widgets; this presents a more
    honest (if approximate) human interaction instead."""
    start_x = target_x + random.uniform(-250, 250)
    start_y = target_y + random.uniform(-150, 150)
    page.mouse.move(start_x, start_y)

    waypoints = random.randint(2, 4)
    for i in range(1, waypoints + 1):
        x = start_x + (target_x - start_x) * (i / waypoints) + random.uniform(-15, 15)
        y = start_y + (target_y - start_y) * (i / waypoints) + random.uniform(-15, 15)
        page.mouse.move(x, y, steps=random.randint(3, 8))
        page.wait_for_timeout(random.randint(40, 150))

    page.mouse.move(target_x, target_y, steps=random.randint(3, 6))
    page.wait_for_timeout(random.randint(150, 450))  # brief hover before acting
    page.mouse.down()
    page.wait_for_timeout(random.randint(40, 120))
    page.mouse.up()


def _human_like_move_and_click(page, locator):
    """Same as _human_like_move_and_click_point, targeting the center-ish of
    `locator`'s bounding box. Falls back to a plain click if a bounding box
    isn't available for any reason."""
    box = locator.bounding_box()
    if not box:
        locator.click(timeout=2000)
        return
    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    _human_like_move_and_click_point(page, target_x, target_y)


# If it's not the checkbox in the top-left of the widget, it's within this
# range - both Turnstile's and hCaptcha's standard layout put the checkbox
# near the left edge of their own iframe, with label text to its right, so
# this is a reasonable target regardless of the widget's exact DOM/class
# names (which aren't discoverable without live inspection).
WIDGET_CHECKBOX_X_FRACTION_RANGE = (0.06, 0.14)
WIDGET_CHECKBOX_Y_FRACTION_RANGE = (0.4, 0.6)
WIDGET_IFRAME_MIN_SIZE = (50, 20)  # (width, height) px - filters out trackers/other tiny iframes


def _click_captcha_checkbox(page, url) -> bool:
    """Look through every frame on the page for a checkbox-like element and
    click it once via _human_like_move_and_click. Returns True if something
    was clicked (so the caller doesn't need to keep retrying), False if
    nothing was clicked this attempt - never raises.

    If no frame has a specifically-selectable checkbox (confirmed to happen
    in production against real Turnstile/hCaptcha pages - CAPTCHA_CHECKBOX_
    SELECTOR simply doesn't match either widget's actual markup), falls back
    to clicking near the left edge of each candidate iframe directly instead
    of giving up."""
    frames = page.frames
    logger.info("%s: looking for a captcha checkbox across %d frame(s)", url, len(frames))
    for frame in frames:
        try:
            locator = frame.locator(CAPTCHA_CHECKBOX_SELECTOR).first
            locator.wait_for(state="visible", timeout=1500)
            page.wait_for_timeout(random.randint(300, 1200))  # a beat before acting
            _human_like_move_and_click(page, locator)
            logger.info("%s: clicked a captcha checkbox (frame: %s)", url, frame.url)
            return True
        except Exception as e:
            logger.info("%s: no clickable checkbox in frame %s (%s)", url, frame.url, e)

    for frame in frames:
        if frame == page.main_frame:
            continue  # not a widget iframe, skip
        try:
            box = frame.frame_element().bounding_box()
            if not box or box["width"] < WIDGET_IFRAME_MIN_SIZE[0] or box["height"] < WIDGET_IFRAME_MIN_SIZE[1]:
                continue
            target_x = box["x"] + box["width"] * random.uniform(*WIDGET_CHECKBOX_X_FRACTION_RANGE)
            target_y = box["y"] + box["height"] * random.uniform(*WIDGET_CHECKBOX_Y_FRACTION_RANGE)
            page.wait_for_timeout(random.randint(300, 1200))
            _human_like_move_and_click_point(page, target_x, target_y)
            logger.info(
                "%s: no checkbox element found - clicked near the left edge of widget iframe %s instead",
                url,
                frame.url,
            )
            return True
        except Exception as e:
            logger.info("%s: couldn't click widget iframe %s (%s)", url, frame.url, e)

    return False


def _proxy_config():
    """Optional proxy, off by default. Set PLAYWRIGHT_PROXY_SERVER (and
    PLAYWRIGHT_PROXY_USERNAME/PLAYWRIGHT_PROXY_PASSWORD if it needs auth) in
    .env to route fetches through it - useful if a datacenter IP's
    reputation with a site's bot-protection ends up being the real blocker
    rather than anything about the browser itself."""
    server = os.environ.get("PLAYWRIGHT_PROXY_SERVER")
    if not server:
        return None
    proxy = {"server": server}
    username = os.environ.get("PLAYWRIGHT_PROXY_USERNAME")
    password = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD")
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy


# Small random pause between fetching each of a venue's URLs within one
# cycle, so requests don't land back-to-back in an obviously scripted burst.
INTER_URL_DELAY_RANGE_SECONDS = (2, 6)


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


def _new_context_init_script(context):
    # Chromium driven via CDP exposes navigator.webdriver=True by default,
    # one of the simplest automation tells; hide it before any page script
    # runs.
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )


def open_persistent_context(user_data_dir: str):
    """Launch a Chromium browser backed by a real, disk-stored profile
    directory that's kept alive and reused across the whole watch loop's
    lifetime, instead of a brand new, zero-history browser being launched
    fresh for every single fetch (the previous behavior, still used by
    fetch_rendered_html() when no context is passed in - see below).

    A completely empty-cookie, empty-history browser hitting a protected
    ticketing endpoint directly, over and over, forever, is itself an
    unusual pattern a real long-term visitor doesn't have. Confirmed
    2026-08-03: a normal manual browser session on the same network hit no
    captcha at all, while the per-request-fresh automated session kept
    getting one - the persistent profile lets Cloudflare/Imperva clearance
    cookies (and just general browsing history) actually accumulate and
    carry over between checks, the way a real user's browser would.

    Returns (playwright, context) - both must be kept alive by the caller
    for as long as the context is in use, and closed together via
    close_persistent_context() on shutdown.
    """
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir,
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
        proxy=_proxy_config(),
        user_agent=USER_AGENT,
        locale=BROWSER_LOCALE,
        timezone_id=BROWSER_TIMEZONE,
        viewport=BROWSER_VIEWPORT,
        extra_http_headers=EXTRA_HTTP_HEADERS,
    )
    _new_context_init_script(context)
    return playwright, context


def close_persistent_context(playwright, context):
    try:
        context.close()
    finally:
        playwright.stop()


def _fetch_with_page(page, url: str, wait_ms: int, timeout_ms: int, capture_screenshot: bool):
    """Shared fetch logic given an already-open page. Does not create or
    close any browser/context/page - that's the caller's responsibility, so
    the same logic works whether the page came from a short-lived context
    (fetch_rendered_html's default) or a long-lived persistent one."""
    response = page.goto(url, timeout=timeout_ms)
    page.wait_for_timeout(wait_ms)

    _wait_out_queue_it(page, url)

    try:
        page.locator(COOKIE_ACCEPT_SELECTOR).click(timeout=3000)
        page.wait_for_timeout(500)
    except Exception:
        pass  # no cookie banner present, or it didn't appear in time

    status = response.status if response else None
    title = (page.title() or "").strip().lower()
    content = page.content()
    lower_content = content.lower()

    if status == 429:
        screenshot = page.screenshot(full_page=True) if capture_screenshot else None
        raise BlockedError(f"Rate limited (429) fetching {url}", screenshot=screenshot)

    checkbox_clicked = False
    if _looks_blocked(title, lower_content):
        checkbox_clicked = _click_captcha_checkbox(page, url)

    extra_waited_ms = 0
    while _looks_blocked(title, lower_content) and extra_waited_ms < CHALLENGE_MAX_EXTRA_WAIT_MS:
        # The widget may not have rendered yet on the first attempt above -
        # keep trying each poll until something actually gets clicked, then
        # stop (no need to keep re-clicking).
        if not checkbox_clicked:
            checkbox_clicked = _click_captcha_checkbox(page, url)
        page.wait_for_timeout(CHALLENGE_POLL_INTERVAL_MS)
        extra_waited_ms += CHALLENGE_POLL_INTERVAL_MS
        title = (page.title() or "").strip().lower()
        content = page.content()
        lower_content = content.lower()

    screenshot = page.screenshot(full_page=True) if capture_screenshot else None

    if any(marker in title for marker in BLOCK_TITLE_MARKERS):
        raise BlockedError(
            f"Blocked by bot-protection challenge fetching {url} (title={title!r})",
            screenshot=screenshot,
        )
    if any(marker in lower_content for marker in BLOCK_CONTENT_MARKERS):
        raise BlockedError(f"Blocked by bot-protection challenge fetching {url}", screenshot=screenshot)
    if status is not None and status >= 400:
        raise FetchError(f"HTTP {status} fetching {url}", screenshot=screenshot)

    return content, screenshot


def fetch_rendered_html(
    url: str,
    wait_ms: int = 6000,
    timeout_ms: int = 30000,
    capture_screenshot: bool = False,
    context=None,
):
    """Fetch a URL with a real browser, return (html, screenshot_bytes_or_None).

    The screenshot is captured before any block check, so it's available on
    BlockedError/FetchError too (via the exception's .screenshot attribute) -
    useful for seeing what an unrecognized interstitial actually looks like.

    If `context` is given (a persistent context from open_persistent_context),
    it's reused as-is - only a new page is opened/closed here, so cookies and
    history carry over between calls. Otherwise (the default, used by /peek
    and decoy browsing) falls back to the original behavior: a brand new,
    throwaway browser+context is launched and torn down for this one fetch.
    """
    if context is not None:
        page = context.new_page()
        try:
            return _fetch_with_page(page, url, wait_ms, timeout_ms, capture_screenshot)
        finally:
            page.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"],
            proxy=_proxy_config(),
        )
        try:
            fresh_context = browser.new_context(
                user_agent=USER_AGENT,
                locale=BROWSER_LOCALE,
                timezone_id=BROWSER_TIMEZONE,
                viewport=BROWSER_VIEWPORT,
                extra_http_headers=EXTRA_HTTP_HEADERS,
            )
            _new_context_init_script(fresh_context)
            page = fresh_context.new_page()
            return _fetch_with_page(page, url, wait_ms, timeout_ms, capture_screenshot)
        finally:
            browser.close()


DECOY_DWELL_MS_RANGE = (8000, 25000)


def decoy_browse(url: str, playwright) -> None:
    """Best-effort: visit a generic, non-ticketing page on the same site and
    linger a bit, purely so this IP's traffic looks like a visitor poking
    around rather than a script that only ever hits one exact URL on a fixed
    schedule. Always its own disposable browser (not the persistent profile),
    deliberately never chained into a real ticket-check session - BFI's
    booking backend was previously found to trigger Turnstile *more*
    aggressively on same-session navigation (see BFI_URLS' comment in
    config.py). Never raises - any failure here is silently logged and
    ignored, since this is optional traffic-pattern hygiene, not part of the
    actual ticket-checking path.

    Takes the already-running `playwright` driver (from
    open_persistent_context, kept alive for the whole process on the main
    thread) rather than starting its own via `sync_playwright()` - Playwright
    disallows two sync-API driver instances in the same thread at once, and
    this is always called from the main thread alongside the persistent
    context. Only the browser+context here are throwaway; the underlying
    driver connection is shared.
    """
    try:
        browser = playwright.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"],
            proxy=_proxy_config(),
        )
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale=BROWSER_LOCALE,
                timezone_id=BROWSER_TIMEZONE,
                viewport=BROWSER_VIEWPORT,
                extra_http_headers=EXTRA_HTTP_HEADERS,
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()
            page.goto(url, timeout=20000)

            dwell_ms = random.randint(*DECOY_DWELL_MS_RANGE)
            page.wait_for_timeout(dwell_ms // 3)
            try:
                page.mouse.wheel(0, random.randint(200, 800))
            except Exception:
                pass  # scroll is a nice-to-have, not essential
            page.wait_for_timeout(dwell_ms - dwell_ms // 3)

            logger.info("decoy browse: visited %s for ~%dms", url, dwell_ms)
        finally:
            browser.close()
    except Exception as e:
        logger.info("decoy browse: failed for %s, ignoring (%r)", url, e)


RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 15


class Watcher:
    """Base class. Subclasses implement parse_url() for one venue's page structure."""

    name = "base"
    display_name = "Base"

    def __init__(self, urls, decoy_urls=None):
        self.urls = urls
        self.decoy_urls = decoy_urls or []
        # Set by main.py to a persistent context from open_persistent_context()
        # so checks reuse one real, aging browser profile instead of a fresh
        # empty one every time. None (the default) falls back to the
        # original per-fetch throwaway browser in fetch_rendered_html().
        self.browser_context = None

    def refresh_urls(self):
        """Override for watchers whose URLs are date-based and need
        recomputing before every check (see BFIWatcher/ScienceMuseumWatcher),
        so a long-running process never needs restarting to pick up a new
        day. No-op by default - self.urls stays whatever was passed in."""
        pass

    def parse_url(self, url: str, html: str) -> dict:
        """Return {item_id: {"status": str, "label": str, "url": str}} for one page."""
        raise NotImplementedError

    def check(self):
        """Fetch + parse all URLs for this venue, kept separate per URL.

        Each URL gets up to RETRY_ATTEMPTS tries, spaced RETRY_WAIT_SECONDS
        apart, before giving up - a queue page, a slow-clearing challenge, or
        any other transient interstitial gets a real chance to resolve within
        the same cycle instead of immediately being treated as a failure.

        Returns (items_by_url, issues). items_by_url maps each URL's stable
        *window index* (not the literal URL string - BFI/Science Museum's
        URLs embed today's date and change daily, so a literal URL is never
        the same twice; the index is what main.py uses to track "have we
        ever gotten a baseline for this window" across days) to its parsed
        {item_id: info} dict for windows that succeeded this cycle - windows
        that failed entirely are simply absent. issues is a list of (url,
        kind, message, screenshot_or_None) with kind in {"fetch_failed",
        "blocked", "parse_broken"}, keyed by the literal URL since that's
        what's useful in a log line or Telegram message. One URL failing
        does not stop the others from being checked.
        """
        self.refresh_urls()
        items_by_url = {}
        issues = []
        for url_index, url in enumerate(self.urls):
            if url_index > 0:
                time.sleep(random.uniform(*INTER_URL_DELAY_RANGE_SECONDS))
            outcome = None  # (kind, message, screenshot)
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                is_last_attempt = attempt == RETRY_ATTEMPTS
                html = None
                screenshot = None

                try:
                    html, screenshot = fetch_rendered_html(
                        url, capture_screenshot=is_last_attempt, context=self.browser_context
                    )
                except BlockedError as e:
                    outcome = ("blocked", str(e), e.screenshot)
                except FetchError as e:
                    outcome = ("fetch_failed", str(e), e.screenshot)
                except Exception as e:
                    # Anything unforeseen (e.g. a Playwright screenshot/navigation
                    # timeout) - treat like any other failed attempt instead of
                    # letting it escape check() entirely and silently skip the
                    # whole venue for this cycle.
                    outcome = ("fetch_failed", f"Unexpected error fetching {url}: {e!r}", None)

                if html is not None:
                    try:
                        items = self.parse_url(url, html)
                        items_by_url[url_index] = items
                        outcome = None
                        break
                    except ParseError as e:
                        outcome = ("parse_broken", str(e), screenshot)
                    except Exception as e:
                        outcome = ("parse_broken", f"Unexpected parser error for {url}: {e!r}", screenshot)

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

        return items_by_url, issues


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
