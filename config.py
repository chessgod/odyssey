"""Non-secret configuration: target URLs, poll interval, jitter."""

CHECK_INTERVAL_SECONDS = 180  # ~3 minutes
JITTER_SECONDS = 45  # random +/- jitter applied to each interval

BFI_URLS = [
    "https://whatson.bfi.org.uk/imax/Online/default.asp?BOparam%3A%3AWScontent%3A%3AloadArticle%3A%3Apermalink=odyssey-the-film-imax-70mm-2026",
    "https://whatson.bfi.org.uk/imax/Online/default.asp?sToken=1%2C95fc2d9e%2C6a692813%2C88998DA5-4B10-4245-B1F9-C78B64EA7508%2C5hfQebENCtKvBZpADdgnjAW9wC8%3D&BOset::WScontent::SearchResultsInfo::current_page=2&doWork::WScontent::getPage=&BOparam::WScontent::getPage::article_id=A0A2A7B6-689F-40DA-A1E4-22F7A5B3E99A",
    "https://whatson.bfi.org.uk/imax/Online/default.asp?sToken=1%2C95fc2d9e%2C6a692813%2C88998DA5-4B10-4245-B1F9-C78B64EA7508%2C5hfQebENCtKvBZpADdgnjAW9wC8%3D&BOset::WScontent::SearchResultsInfo::current_page=3&doWork::WScontent::getPage=&BOparam::WScontent::getPage::article_id=A0A2A7B6-689F-40DA-A1E4-22F7A5B3E99A",
]

SCIENCE_MUSEUM_URLS = [
    "https://my.sciencemuseum.org.uk/events?view=calendar&kid=794&startdate=01-07-2026",
]
