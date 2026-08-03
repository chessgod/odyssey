"""Non-secret configuration: target URLs, poll interval, jitter."""

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

#   Each URL is a date-range-filtered BFI search, loaded as a fresh
#   top-level navigation. Turnstile only seems to trigger on navigating to
#   the search backend *within* a session that already loaded another page
#   (pagination links, date-filter clicks) — a brand new session going
#   straight to a search URL loads cleanly. Windows are sized to stay
#   comfortably under the 50-result page-size limit based on live testing
#   on 2026-07-31 (watchers/bfi.py alerts if a window ever fills up, rather
#   than silently truncating); review/adjust these as dates pass or BFI's
#   schedule changes.
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
BFI_DATE_WINDOWS = [
    ("2026-7-29", "2026-8-7"),
    ("2026-8-8", "2026-8-17"),
    ("2026-8-18", "2026-8-27"),
    ("2026-8-28", "2026-9-30"),
    ("2026-10-1", "2027-12-31"),
]
BFI_URLS = [_BFI_SEARCH_URL_TEMPLATE.format(frm=frm, to=to) for frm, to in BFI_DATE_WINDOWS]

SCIENCE_MUSEUM_URLS = [
    "https://my.sciencemuseum.org.uk/events?view=calendar&kid=794&startdate=01-07-2026",
]

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
# actual ticket check - see the note on BFI_URLS above about same-session
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
