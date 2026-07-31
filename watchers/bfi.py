"""BFI parser.

Parses date-range-filtered search results pages. These are constructed URLs
(see config.BFI_URLS) that a fresh top-level navigation can load cleanly —
unlike the paginated search results or clicking a date in the on-page
calendar, both of which go through a search backend gated by an interactive
Cloudflare Turnstile challenge that doesn't auto-resolve when reached via a
same-session click/AJAX navigation. A brand new `page.goto()` straight to
the same search URL, in a session that never touched page 1 first, loads
cleanly instead — Turnstile appears to trigger on same-session navigation,
not on the URL/endpoint itself.

Each date-range search returns every film/event playing BFI IMAX in that
window, not just this one, so results are filtered per-item by
config.BFI_EXPECTED_KEYWORD rather than checking the page as a whole.
"""

import logging
import re

from bs4 import BeautifulSoup

import config
from watchers.base import ParseError, Watcher

logger = logging.getLogger(__name__)

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
# Search results pages cap at 50 items/page. config.BFI_URLS' date windows
# were sized to stay under this, but if BFI's schedule gets denser this could
# silently truncate — so it's treated as a parse failure instead, worth
# alerting on, rather than quietly missing whatever's on page 2+.
RESULTS_PAGE_SIZE = 50


class BFIWatcher(Watcher):
    name = "bfi"
    display_name = "BFI"

    def parse_url(self, url: str, html: str) -> dict:
        title_match = TITLE_RE.search(html)
        title = title_match.group(1).strip() if title_match else ""
        if "search results" not in title.lower():
            raise ParseError(
                f"Page title {title!r} doesn't look like a BFI search results page "
                "— wrong page, redirect, or the site changed?"
            )

        soup = BeautifulSoup(html, "html.parser")
        result_items = soup.select(".result-box-item")
        pagination_pages = soup.select(".pagination .page-item")
        if len(result_items) >= RESULTS_PAGE_SIZE and len(pagination_pages) > 1:
            raise ParseError(
                f"Got {len(result_items)} results with {len(pagination_pages)} pagination "
                f"pages for {url} — this date window is likely truncated at the page-size "
                "limit and needs narrowing in config.py"
            )

        items = {}
        for el in result_items:
            name_el = el.select_one(".item-name")
            date_el = el.select_one(".start-date")
            link_el = el.select_one(".item-link")
            if not date_el or not link_el:
                raise ParseError("result item missing expected .start-date or .item-link")

            name = name_el.get_text(strip=True) if name_el else ""
            if config.BFI_EXPECTED_KEYWORD not in name.lower():
                continue  # a different film/event sharing this date window

            date_text = date_el.get_text(strip=True)
            status_text = link_el.get_text(strip=True).lower()
            if "sold out" in status_text:
                status = "sold_out"
            elif "buy" in status_text:
                status = "available"
            else:
                logger.warning(
                    "bfi: unrecognized status %r for %s, skipping this performance",
                    status_text,
                    date_text,
                )
                continue

            items[date_text] = {
                "status": status,
                "label": f"BFI Odyssey IMAX 70mm — {date_text}",
                "url": url,
            }
        return items
