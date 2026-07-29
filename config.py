"""Non-secret configuration: target URLs, poll interval, jitter."""

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

#   The permalink URL alone embeds a `calendar_days` JSON array covering a
#   rolling ~6-week window of per-date availability. Pagination URLs (page 2,
#   3, ...) are deliberately not used: they carry a session-bound sToken and
#   are gated by an interactive Cloudflare Turnstile challenge.
BFI_URLS = [
    "https://whatson.bfi.org.uk/imax/Online/default.asp?BOparam%3A%3AWScontent%3A%3AloadArticle%3A%3Apermalink=odyssey-the-film-imax-70mm-2026",
]

SCIENCE_MUSEUM_URLS = [
    "https://my.sciencemuseum.org.uk/events?view=calendar&kid=794&startdate=01-07-2026",
]
