import requests
from scrapling import Selector
import urllib.parse

class ScrapingEngine:
    """
    Wrapper for Scrapling to ensure headers, cookies, and settings
    are applied consistently across all scraping activities using requests session
    and wrapped Scrapling Selector.
    """
    def __init__(self, config: dict):
        self.config = config
        self.settings = config.get("settings", {})
        self.cookies = config.get("active_cookies", {})
        self.user_agent = self.settings.get("user_agent")

    def get_headers(self) -> dict:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        return headers

    def get_cookies(self) -> dict:
        # Automatically URL-decode cookie values when preparing requests
        return {k: urllib.parse.unquote(v) for k, v in self.cookies.items()}

    def get_delay(self) -> float:
        return float(self.settings.get("request_delay_seconds", 2.0))

    def fetch_page(self, url: str):
        """
        Fetch a page using requests and wrap it in a Scrapling Selector.
        """
        headers = self.get_headers()
        cookies = self.get_cookies()

        session = requests.Session()
        session.headers.update(headers)
        
        response = session.get(url, cookies=cookies)
        
        # Wrap response in Scrapling Selector
        # Use response.content (bytes) to avoid lxml encoding declaration errors
        selector = Selector(response.content)
        
        # Attach url and status for compatibility with scraper logic
        selector.url = response.url
        selector.status = response.status_code
        return selector
