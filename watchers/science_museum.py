"""Science Museum parser.

Parses the rendered Tessitura events calendar. Each performance is a
<li data-tn-performance-id="..."> with an optional status span present only
when sold out; its absence means the performance is bookable.

The page also renders a duplicate mobile/list-view copy of every event
outside the main calendar table (same data-tn-performance-id, but not
wrapped in a dated day container) - item selection is scoped to
.tn-events-calendar__table specifically to avoid ever touching those
duplicates, since (unlike the desktop copy) they carry no date information.

Each desktop event <li> sits inside a <div class="tn-events-calendar__day"
data-testid="events-calendar-day-YYYY-MM-DD">, which is where the actual
calendar date lives - the event item itself only carries a time
(.tn-events-calendar__event-time), not a date. Alert labels combine both
(2026-08-06: previously labels only had a bare time like "14:30" with no
date at all, useless on its own in a Telegram alert).
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

import config
from watchers.base import ParseError, Watcher

DAY_TESTID_RE = re.compile(r"events-calendar-day-(\d{4}-\d{2}-\d{2})")


class ScienceMuseumWatcher(Watcher):
    name = "science_museum"
    display_name = "Science Museum"

    def refresh_urls(self):
        self.urls = config.science_museum_urls()

    def parse_url(self, url: str, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        table = soup.select_one(".tn-events-calendar__table")
        if not table:
            raise ParseError("tn-events-calendar__table container not found")

        # The page also renders a duplicate mobile/list-view copy of every
        # event outside this table (same data-tn-performance-id, but not
        # wrapped in a dated day container) - scoping to the table avoids
        # ever touching those duplicates, rather than deduplicating by
        # perf_id afterward and hoping the desktop copy wins.
        items = {}
        for el in table.select("li.tn-events-calendar__day-event-list-item"):
            perf_id = el.get("data-tn-performance-id")
            if not perf_id:
                raise ParseError("event item missing data-tn-performance-id")

            day_div = el.find_parent("div", class_="tn-events-calendar__day")
            match = DAY_TESTID_RE.search(day_div.get("data-testid", "")) if day_div else None
            if not match:
                raise ParseError(
                    f"event item {perf_id} has no containing day with a parseable date"
                )
            date_text = datetime.strptime(match.group(1), "%Y-%m-%d").strftime("%A %d %B %Y")

            name_el = el.select_one(".tn-events-calendar__event-name")
            time_el = el.select_one(".tn-events-calendar__event-time")
            status_el = el.select_one(".tn-events-calendar__event-status")
            link_el = el.select_one("a")

            name = name_el.get_text(strip=True) if name_el else "Unknown event"
            time_text = time_el.get_text(strip=True).lstrip(", ") if time_el else ""
            href = link_el["href"] if link_el and link_el.has_attr("href") else url
            status = "sold_out" if status_el else "available"

            when = f"{date_text}, {time_text}" if time_text else date_text
            items[perf_id] = {
                "status": status,
                "label": f"Science Museum — {name} — {when}".strip(),
                "url": href,
            }

        keyword = config.SCIENCE_MUSEUM_EXPECTED_KEYWORD
        if items and not any(keyword in v["label"].lower() for v in items.values()):
            raise ParseError(
                f"None of the {len(items)} items mention {keyword!r} "
                "— wrong event, or the calendar now covers something else?"
            )
        return items
