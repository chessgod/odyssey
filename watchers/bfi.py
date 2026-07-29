"""BFI parser.

Parses the visible results list on the film's permalink page (page 1 only)
— the same "Sold out!" text a human sees. This is a deliberate scope
reduction: BFI's paginated search results (page 2, 3, ...) and clicking a
specific date in the calendar widget both go through a search backend gated
by an interactive Cloudflare Turnstile challenge that doesn't auto-resolve,
so they're not reachable here. An earlier version of this parser read a
`calendar_days` JSON block embedded in the page instead, assuming its codes
meant per-date availability — that assumption turned out to be wrong (it
showed many dates as available that were actually sold out, unverifiable
against anything real), so it's been dropped in favour of only trusting
data that's directly visible and checkable. This means only the ~5 nearest
upcoming performances are tracked, not the full run.
"""

import re

from bs4 import BeautifulSoup

import config
from watchers.base import ParseError, Watcher

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


class BFIWatcher(Watcher):
    name = "bfi"
    display_name = "BFI"

    def parse_url(self, url: str, html: str) -> dict:
        title_match = TITLE_RE.search(html)
        title = title_match.group(1).strip() if title_match else ""
        if config.BFI_EXPECTED_KEYWORD not in title.lower():
            raise ParseError(
                f"Page title {title!r} doesn't mention {config.BFI_EXPECTED_KEYWORD!r} "
                "— wrong page, redirect, or the event changed?"
            )

        soup = BeautifulSoup(html, "html.parser")
        result_items = soup.select(".result-box-item")
        if not result_items:
            raise ParseError("no .result-box-item elements found on BFI page")

        items = {}
        for el in result_items:
            date_el = el.select_one(".start-date")
            link_el = el.select_one(".item-link")
            if not date_el or not link_el:
                raise ParseError("result item missing expected .start-date or .item-link")

            date_text = date_el.get_text(strip=True)
            is_sold_out = bool(link_el.select_one(".unavailable-message")) or "soldout" in (
                link_el.get("class") or []
            )
            status = "sold_out" if is_sold_out else "available"

            items[date_text] = {
                "status": status,
                "label": f"BFI Odyssey IMAX 70mm — {date_text}",
                "url": url,
            }
        return items
