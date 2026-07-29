"""BFI parser.

Parses the `calendar_days` array embedded in the film's permalink page
(inside the page's `articleContext.searchCalendarFilters` JS object) rather
than the paginated search results, which are session-token gated and blocked
by an interactive Cloudflare challenge. This array gives per-date aggregate
availability across a rolling ~6-week window without any pagination.
"""

import re

from watchers.base import ParseError, Watcher

CALENDAR_BLOCK_RE = re.compile(
    r'name\s*:\s*"calendar_days".*?values\s*:\s*\[\s*(.*?)\s*\]\s*\}',
    re.S,
)
ENTRY_RE = re.compile(r'\["([^"]+)",\s*"(\d+)"\]')

# Legend: 1=Excellent, 2=Good, 3=Limited, 4=Sold Out (confirmed against the
# live page: dates showing "Sold out!" in the results list carry code 4).
SOLD_OUT_CODE = "4"
KNOWN_CODES = {"1", "2", "3", "4"}


class BFIWatcher(Watcher):
    name = "bfi"
    display_name = "BFI"

    def parse_url(self, url: str, html: str) -> dict:
        match = CALENDAR_BLOCK_RE.search(html)
        if not match:
            raise ParseError("calendar_days block not found in BFI page")

        entries = ENTRY_RE.findall(match.group(1))
        if not entries:
            raise ParseError("calendar_days block found but no date entries parsed")

        items = {}
        for date_str, code in entries:
            if code not in KNOWN_CODES:
                raise ParseError(f"Unexpected availability code {code!r} for {date_str}")
            date_only = date_str.split("T")[0]
            status = "sold_out" if code == SOLD_OUT_CODE else "available"
            items[date_only] = {
                "status": status,
                "label": f"BFI Odyssey IMAX 70mm — {date_only}",
                "url": url,
            }
        return items
