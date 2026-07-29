"""Science Museum parser.

Parses the rendered Tessitura events calendar. Each performance is a
<li data-tn-performance-id="..."> with an optional status span present only
when sold out; its absence means the performance is bookable. The calendar
renders duplicate markup for mobile/desktop layouts, so items are naturally
deduplicated by performance id via dict keys.
"""

from bs4 import BeautifulSoup

import config
from watchers.base import ParseError, Watcher


class ScienceMuseumWatcher(Watcher):
    name = "science_museum"
    display_name = "Science Museum"

    def parse_url(self, url: str, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        if not soup.select_one(".tn-events-calendar__table"):
            raise ParseError("tn-events-calendar__table container not found")

        items = {}
        for el in soup.select("li.tn-events-calendar__day-event-list-item"):
            perf_id = el.get("data-tn-performance-id")
            if not perf_id:
                raise ParseError("event item missing data-tn-performance-id")

            name_el = el.select_one(".tn-events-calendar__event-name")
            time_el = el.select_one(".tn-events-calendar__event-time")
            status_el = el.select_one(".tn-events-calendar__event-status")
            link_el = el.select_one("a")

            name = name_el.get_text(strip=True) if name_el else "Unknown event"
            time_text = time_el.get_text(strip=True).lstrip(", ") if time_el else ""
            href = link_el["href"] if link_el and link_el.has_attr("href") else url
            status = "sold_out" if status_el else "available"

            items[perf_id] = {
                "status": status,
                "label": f"Science Museum — {name} {time_text}".strip(),
                "url": href,
            }

        keyword = config.SCIENCE_MUSEUM_EXPECTED_KEYWORD
        if items and not any(keyword in v["label"].lower() for v in items.values()):
            raise ParseError(
                f"None of the {len(items)} items mention {keyword!r} "
                "— wrong event, or the calendar now covers something else?"
            )
        return items
