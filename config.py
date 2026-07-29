"""Non-secret configuration: target URLs, poll interval, jitter."""

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

#   Only the permalink URL (page 1) is watched — its visible results list is
#   the only BFI data reachable without hitting an interactive Cloudflare
#   Turnstile challenge that doesn't auto-resolve (triggered by pagination
#   URLs and by date-filter clicks alike). This means only the ~5 nearest
#   upcoming performances are tracked, not the full run.
BFI_URLS = [
    "https://whatson.bfi.org.uk/imax/Online/default.asp?BOparam%3A%3AWScontent%3A%3AloadArticle%3A%3Apermalink=odyssey-the-film-imax-70mm-2026",
]

SCIENCE_MUSEUM_URLS = [
    "https://my.sciencemuseum.org.uk/events?view=calendar&kid=794&startdate=01-07-2026",
]

# Sanity-check keyword each parser looks for in the fetched page/items before
# trusting its data. Guards against silently treating a wrong or unexpected
# page (e.g. a redirect, or the site swapping the event behind the same URL)
# as valid availability data. Case-insensitive.
BFI_EXPECTED_KEYWORD = "odyssey"
SCIENCE_MUSEUM_EXPECTED_KEYWORD = "odyssey"
