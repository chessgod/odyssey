"""Non-secret configuration: target URLs, poll interval, jitter."""

import datetime

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

# Each URL is a date-range-filtered BFI search, loaded as a fresh top-level
# navigation. Turnstile only seems to trigger on navigating to the search
# backend *within* a session that already loaded another page (pagination
# links, date-filter clicks) — a brand new session going straight to a
# search URL loads cleanly. Windows are sized to stay comfortably under the
# 50-result page-size limit based on live testing on 2026-07-31
# (watchers/bfi.py alerts if a window ever fills up, rather than silently
# truncating). The *dates* are computed fresh from today() on every call to
# bfi_urls() below - hardcoding a fixed start date meant it went stale
# within days and had to be hand-edited, which also meant a stale window got
# searched (useless, and an odd-looking query for a real visitor to make).
# Only the window *sizes* in _BFI_WINDOW_DAY_OFFSETS need revisiting if
# BFI's schedule gets denser.
_BFI_ARTICLE_SEARCH_ID = "49C49C83-6BA0-420C-A784-9B485E36E2E0"
_BFI_SEARCH_URL_TEMPLATE = (
    "https://whatson.bfi.org.uk/imax/Online/default.asp?"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Avenue_filter=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Acity_filter=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Amonth_filter=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Aobject_type_filter=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Acategory_filter=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_from=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_to=&"
    "doWork%3A%3AWScontent%3A%3Asearch=1&"
    f"BOparam%3A%3AWScontent%3A%3Asearch%3A%3Aarticle_search_id={_BFI_ARTICLE_SEARCH_ID}&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_criteria=&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_from={frm}&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_to={to}"
)
# (start_offset, end_offset) in days-from-today for each window - same
# 10/10/10/~35-day/long-tail spacing as the original hand-picked windows,
# just anchored to "today" instead of a fixed date so it never goes stale.
_BFI_WINDOW_DAY_OFFSETS = [(0, 10), (10, 20), (20, 30), (30, 65), (65, 730)]


def _fmt_bfi_date(d: datetime.date) -> str:
    """BFI's search backend expects "{year}-{month}-{day}" with no leading
    zeros (confirmed against previously-working values, e.g. "2026-8-7").
    Built manually rather than via strftime's no-padding flags (%-d), which
    aren't portable to Windows."""
    return f"{d.year}-{d.month}-{d.day}"


def bfi_urls(today: datetime.date = None) -> list:
    """The 5 BFI search URLs, with date windows computed fresh from today
    (or the given date, for testing) so this never needs manual updating."""
    today = today or datetime.date.today()
    windows = [
        (today + datetime.timedelta(days=start), today + datetime.timedelta(days=end - 1))
        for start, end in _BFI_WINDOW_DAY_OFFSETS
    ]
    return [
        _BFI_SEARCH_URL_TEMPLATE.format(frm=_fmt_bfi_date(frm), to=_fmt_bfi_date(to))
        for frm, to in windows
    ]


def science_museum_urls(today: datetime.date = None) -> list:
    """The Science Museum calendar URL, with startdate computed fresh from
    today (or the given date, for testing) for the same reason as above."""
    today = today or datetime.date.today()
    startdate = today.strftime("%d-%m-%Y")  # DD-MM-YYYY, the UK site's native format
    return [f"https://my.sciencemuseum.org.uk/events?view=calendar&kid=794&startdate={startdate}"]

# Sanity-check keyword each parser looks for in the fetched page/items before
# trusting its data. Guards against silently treating a wrong or unexpected
# page (e.g. a redirect, or the site swapping the event behind the same URL)
# as valid availability data. Case-insensitive.
BFI_EXPECTED_KEYWORD = "odyssey"
SCIENCE_MUSEUM_EXPECTED_KEYWORD = "odyssey"

# Generic, non-ticketing pages on each site's own protected zone, used for
# occasional decoy browsing between checks (see main.py: maybe_decoy_browse)
# so this IP's traffic looks like a visitor poking around the site, not a
# script that only ever hits one exact URL on a fixed schedule. Verified
# live on 2026-08-03 to load cleanly without tripping either site's
# challenge. Deliberately NOT chained into the same browser session as an
# actual ticket check - see the note on bfi_urls() above about same-session
# navigation triggering Turnstile more aggressively; decoy_browse() always
# runs as its own disposable session instead.
BFI_DECOY_URLS = [
    "https://whatson.bfi.org.uk/imax/Online/article/imax70mm",
]
SCIENCE_MUSEUM_DECOY_URLS = [
    "https://my.sciencemuseum.org.uk/",
]

# Fraction of cycles that spend part of the idle wait on a decoy browse
# instead of just sleeping - "sometimes, not every time" is the point.
DECOY_BROWSE_PROBABILITY = 0.4
