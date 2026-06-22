from scrapling import Fetcher
from scrapling.fetchers import StealthyFetcher

class ScrapingEngine:
    """
    Wrapper for Scrapling to ensure headers, cookies, and settings
    are applied consistently across all scraping activities.
    """
    def __init__(self, config: dict):
        self.config = config
        self.settings = config.get("settings", {})
        self.cookies = config.get("active_cookies", {})
        self.user_agent = self.settings.get("user_agent")

    def get_headers(self) -> dict:
        headers = {}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        return headers

    def get_cookies(self) -> dict:
        return self.cookies

    def get_delay(self) -> float:
        return float(self.settings.get("request_delay_seconds", 2.0))

    def fetch_page(self, url: str, use_stealth: bool = False):
        """
        Fetch a page using Scrapling Fetcher or StealthyFetcher.
        """
        headers = self.get_headers()
        cookies = self.get_cookies()

        if use_stealth:
            # StealthyFetcher uses Camoufox/Playwright. If not installed, it will fail
            # so we wrap it.
            try:
                return StealthyFetcher.get(url, headers=headers, cookies=cookies)
            except Exception as e:
                # Fallback to standard fetcher if StealthyFetcher fails due to missing binaries
                return Fetcher.get(url, headers=headers, cookies=cookies)
        else:
            return Fetcher.get(url, headers=headers, cookies=cookies)
