import urllib.parse
from scrapling import Selector
from scrapling.fetchers import StealthyFetcher

class ScrapingEngine:
    """
    Wrapper for Scrapling to ensure headers, cookies, and settings
    are applied consistently across all scraping activities using StealthyFetcher
    and wrapped Scrapling Selector.
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

    def get_cookies(self) -> list:
        # Format cookies in Playwright's format
        playwright_cookies = []
        for k, v in self.cookies.items():
            playwright_cookies.append({
                "name": k,
                "value": urllib.parse.unquote(v),
                "domain": ".facebook.com",
                "path": "/"
            })
        return playwright_cookies

    def get_delay(self) -> float:
        return float(self.settings.get("request_delay_seconds", 2.0))

    def fetch_page(self, url: str, page_action=None):
        """
        Fetch a page using Scrapling's StealthyFetcher and return a Selector.
        """
        cookies = self.get_cookies()
        
        # Use StealthyFetcher.fetch
        response = StealthyFetcher.fetch(
            url,
            cookies=cookies,
            headless=True,
            network_idle=True,
            page_action=page_action
        )
        
        # Wrap HTML content in Scrapling Selector
        selector = Selector(response.html_content)
        
        # Attach details for scraper compatibility
        selector.url = response.url
        selector.status = response.status
        # Attach raw response object so scraper can access details if needed
        selector.response = response
        
        return selector
