"""Non-secret configuration: target URLs, poll interval, jitter."""

import datetime

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

# A single keyword-filtered BFI search, loaded as a fresh top-level
# navigation. Turnstile only seems to trigger on navigating to the search
# backend *within* a session that already loaded another page (pagination
# links, date-filter clicks) — a brand new session going straight to a
# search URL loads cleanly.
#
# Previously this was 5 separate date-window URLs covering everything
# playing IMAX (filtered client-side by BFI_EXPECTED_KEYWORD afterward).
# Switched 2026-08-04 to searching "The Odyssey" directly via BFI's own
# search_criteria field instead - far more precise, and cuts 5 requests/cycle
# down to 1, which matters a lot given BFI's captcha exposure scales with
# request volume. The tradeoff, confirmed by live testing that day: BFI caps
# search results at 50/page, and "The Odyssey" alone was already filling a
# page within ~10-15 days given how many screenings it has - so this only
# covers the next ~10 days, not the whole run. That's a deliberate choice
# (discussed and accepted) over going back to multiple windows for full
# coverage; if a sold-out date reopens further out than that, this won't
# catch it. Revisit if that tradeoff stops being acceptable.
#
# BFI_DAYS_AHEAD is the only thing that would need adjusting if BFI's
# schedule gets denser and 50 results starts getting reached within fewer
# than ~10 days (watchers/bfi.py alerts if that ever happens, rather than
# silently truncating). The date itself is computed fresh from today() on
# every call to bfi_urls() below, so it never goes stale/needs hand-editing.
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
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_criteria=The+Odyssey&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_from={frm}&"
    "BOset%3A%3AWScontent%3A%3ASearchCriteria%3A%3Asearch_to={to}"
)
BFI_DAYS_AHEAD = 10


def _fmt_bfi_date(d: datetime.date) -> str:
    """BFI's search backend expects "{year}-{month}-{day}" with no leading
    zeros (confirmed against previously-working values, e.g. "2026-8-7").
    Built manually rather than via strftime's no-padding flags (%-d), which
    aren't portable to Windows."""
    return f"{d.year}-{d.month}-{d.day}"


def bfi_urls(today: datetime.date = None) -> list:
    """The single BFI search URL, covering today through
    today + BFI_DAYS_AHEAD - computed fresh on every call so it never goes
    stale."""
    today = today or datetime.date.today()
    to = today + datetime.timedelta(days=BFI_DAYS_AHEAD - 1)
    return [_BFI_SEARCH_URL_TEMPLATE.format(frm=_fmt_bfi_date(today), to=_fmt_bfi_date(to))]


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
