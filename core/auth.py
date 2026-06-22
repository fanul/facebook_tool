import json
import re
from scrapling import Fetcher

def parse_cookie_string(cookie_str: str) -> dict:
    """
    Parse cookie input. It intelligently handles:
    1. JSON List of cookie dicts (e.g. from browser extension exports like EditThisCookie).
    2. JSON Object/Dict of key-value pairs.
    3. Raw cookie strings (e.g. key1=val1; key2=val2).
    """
    cookies = {}
    if not cookie_str:
        return cookies

    cookie_str = cookie_str.strip()

    # Try parsing as JSON first
    if cookie_str.startswith('[') or cookie_str.startswith('{'):
        try:
            parsed_json = json.loads(cookie_str)
            if isinstance(parsed_json, list):
                # Standard array of cookie dicts: [{"name": "c_user", "value": "1280232621"}, ...]
                for item in parsed_json:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies[item["name"]] = str(item["value"])
            elif isinstance(parsed_json, dict):
                # Simple key-value dictionary: {"c_user": "1280232621", ...}
                for k, v in parsed_json.items():
                    cookies[k] = str(v)
            if cookies:
                return cookies
        except Exception:
            pass  # Fallback to parsing as raw string if JSON parsing fails

    # Fallback to parsing as raw key=value string
    # Remove leading/trailing whitespaces and split by semicolon
    pairs = cookie_str.split(';')
    for pair in pairs:
        if '=' in pair:
            key, val = pair.split('=', 1)
            cookies[key.strip()] = val.strip()
    return cookies

def check_facebook_login(cookies: dict, user_agent: str = None) -> tuple[bool, str]:
    """
    Verify if the provided Facebook cookies are valid by checking mbasic.facebook.com/home.php.
    Returns (is_logged_in, status_message)
    """
    if not cookies:
        return False, "Cookies are empty."
        
    url = "https://mbasic.facebook.com/home.php"
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
        
    try:
        # Use scrapling Fetcher for a quick lightweight HTTP check
        response = Fetcher.get(url, cookies=cookies, headers=headers)
        
        # Check redirects or login indicators
        final_url = response.url
        if "login" in final_url or "login.php" in final_url:
            return False, f"Session expired or redirected to login: {final_url}"
            
        # Check HTML content for login form or logout link
        logout_link = response.css('a[href*="logout.php"]').get()
        login_form = response.css('form[action*="login"]').get()
        
        if login_form and not logout_link:
            return False, "Detected login form on the page. Cookies might be invalid."
            
        if logout_link or response.css('a[href*="menu/bookmark"]').get():
            return True, "Successfully authenticated with Facebook!"
            
        # Fallback check: check for common links
        if response.css('a[href*="/messages/"]').get() or response.css('a[href*="/notifications.php"]').get():
            return True, "Successfully authenticated with Facebook!"
            
        # Fallback check 2: text search
        text_lower = response.text.lower()
        if "logout" in text_lower or "log out" in text_lower or "keluar" in text_lower:
            return True, "Successfully authenticated with Facebook!"
            
        return False, "Could not confirm login status. The cookies might be invalid or restricted."
    except Exception as e:
        return False, f"Error connecting to Facebook: {str(e)}"
