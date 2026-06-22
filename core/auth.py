import json
import re
import urllib.parse
import requests
from scrapling import Selector

def parse_cookie_string(cookie_str: str) -> dict:
    """
    Parse cookie input. It intelligently handles:
    1. JSON List of cookie dicts (e.g. from browser extension exports like EditThisCookie).
    2. JSON Object/Dict of key-value pairs.
    3. Raw cookie strings (e.g. key1=val1; key2=val2).
    It also sanitizes newlines and spaces introduced by terminal copy-paste actions
    and automatically decodes URL-encoded values (like colons in xs).
    """
    cookies = {}
    if not cookie_str:
        return cookies

    # Strip whitespace and Unicode BOM if present (common in Notepad files)
    cookie_str = cookie_str.strip().lstrip('\ufeff')

    def extract_from_json(parsed_json) -> dict:
        result = {}
        if isinstance(parsed_json, list):
            for item in parsed_json:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    result[item["name"]] = urllib.parse.unquote(str(item["value"]))
        elif isinstance(parsed_json, dict):
            for k, v in parsed_json.items():
                result[k] = urllib.parse.unquote(str(v))
        return result

    # 1. Try parsing as standard JSON
    try:
        parsed_json = json.loads(cookie_str)
        extracted = extract_from_json(parsed_json)
        if extracted:
            return extracted
    except Exception:
        pass

    # 2. Try parsing as JSON after stripping all whitespace/newlines (fixes console wrapping/copy-paste breaks)
    try:
        cleaned_json_str = re.sub(r'\s+', '', cookie_str)
        parsed_json = json.loads(cleaned_json_str)
        extracted = extract_from_json(parsed_json)
        if extracted:
            return extracted
    except Exception:
        pass

    # 3. Fallback to parsing as raw key=value string
    # Remove all formatting newlines and carriage returns
    cleaned_raw = cookie_str.replace('\r', '').replace('\n', '')
    pairs = cleaned_raw.split(';')
    for pair in pairs:
        if '=' in pair:
            key, val = pair.split('=', 1)
            # Remove any internal spaces introduced by word-wrapping and decode
            clean_key = key.replace(' ', '').strip()
            clean_val = urllib.parse.unquote(val.replace(' ', '').strip())
            if clean_key:
                cookies[clean_key] = clean_val
    return cookies

def check_facebook_login(cookies: dict, user_agent: str = None) -> tuple[bool, str]:
    """
    Verify if the provided Facebook cookies are valid by checking mbasic.facebook.com/home.php.
    Returns (is_logged_in, status_message)
    """
    if not cookies:
        return False, "Cookies are empty."
        
    url = "https://mbasic.facebook.com/home.php"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if user_agent:
        headers["User-Agent"] = user_agent
        
    try:
        # Use requests to fetch page as it handles cookie domains and redirects more robustly
        session = requests.Session()
        session.headers.update(headers)
        
        # Decode cookie values
        decoded_cookies = {k: urllib.parse.unquote(v) for k, v in cookies.items()}
        response = session.get(url, cookies=decoded_cookies)
        
        final_url = response.url.lower()
        
        # If redirected to a login or checkpoint page, validation failed
        if "login" in final_url or "login.php" in final_url or "checkpoint" in final_url:
            return False, f"Session expired or redirected to login/checkpoint: {response.url}"
            
        # Wrap response in Scrapling Selector for consistent parsing
        # Use response.content (bytes) to avoid lxml encoding declaration errors
        selector = Selector(response.content)
            
        # Inspect HTML for a login form (email and password inputs)
        has_email_input = selector.css('input[name="email"]').get() is not None
        has_pass_input = selector.css('input[name="pass"]').get() is not None
        if has_email_input and has_pass_input:
            return False, "Cookies are invalid (redirected to a login form)."

        # Check for logout links or bookmark menus (mbasic)
        logout_link = selector.css('a[href*="logout.php"]').get()
        login_form = selector.css('form[action*="login"]').get()
        
        if login_form and not logout_link:
            return False, "Detected login form container on the page. Cookies might be invalid."
            
        if logout_link or selector.css('a[href*="menu/bookmark"]').get():
            return True, "Successfully authenticated with Facebook!"
            
        # Check if we were redirected to the main desktop home page/feed (which is a success indicator)
        if "facebook.com/home.php" in final_url or (
            "facebook.com" in final_url and ("_rdr" in final_url or "ref" in final_url)
        ):
            return True, "Successfully authenticated with Facebook (redirected to home feed)!"
            
        # Fallback check: check for common links
        if selector.css('a[href*="/messages/"]').get() or selector.css('a[href*="/notifications.php"]').get():
            return True, "Successfully authenticated with Facebook!"
            
        # Fallback check 2: text search
        text_lower = response.text.lower()
        if "logout" in text_lower or "log out" in text_lower or "keluar" in text_lower:
            return True, "Successfully authenticated with Facebook!"
            
        # If we successfully loaded the home page without any login page indicators:
        if "home.php" in final_url or final_url == "https://mbasic.facebook.com/" or final_url == "https://www.facebook.com/":
            return True, "Successfully authenticated with Facebook (no login form detected)!"

        return False, "Could not confirm login status. The cookies might be invalid or restricted."
    except Exception as e:
        return False, f"Error connecting to Facebook: {str(e)}"
