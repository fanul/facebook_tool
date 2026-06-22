#!/usr/bin/env python3
"""
Non-interactive Facebook post scraper wrapper.
Usage: python3 run_scraper.py <username_or_id> <limit> [export_format]
Example: python3 run_scraper.py RhenovattioDejaVu 10 JSON
"""
import sys
import os
import json
import re

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.auth import parse_cookie_string
from tools.fb_post_scraper.scraper import FacebookPostScraper


class MockQuestion:
    """Mock questionary class that returns predefined values via .ask()"""
    def __init__(self, value):
        self._value = value
    
    def ask(self):
        return self._value


def run_scraper(target, limit=10, export_format="JSON", cookies_file="cookies.json", config_file="config.json"):
    """Run scraper non-interactively."""
    
    # Load config
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Load cookies
    with open(cookies_file, 'r') as f:
        raw = f.read()
    cookies = parse_cookie_string(raw)
    config['active_cookies'] = cookies
    
    # Validate cookies (skip mbasic check, use curl_cffi instead)
    print(f"Cookie check: Using {len(cookies)} cookies (c_user={cookies.get('c_user', 'N/A')[:10]})")
    
    # Create scraper instance
    scraper = FacebookPostScraper()
    
    # Mock questionary for non-interactive mode
    import questionary
    original_select = questionary.select
    original_text = questionary.text
    original_confirm = questionary.confirm
    
    settings = config.get('settings', {})
    max_wait = settings.get('max_scroll_wait_seconds', 180)
    freq_min = settings.get('scroll_freq_min', 1)
    freq_max = settings.get('scroll_freq_max', 3)
    
    def mock_text(message="", default="", **kwargs):
        message_str = str(message)
        if "Facebook Username" in message_str or "Profile ID" in message_str or "Profile URL" in message_str:
            return MockQuestion(target)
        elif "max number" in message_str.lower():
            return MockQuestion(str(limit))
        elif "waiting" in message_str.lower() or "max anti-bot" in message_str.lower():
            return MockQuestion(str(max_wait))
        elif "upper bound" in message_str.lower():
            return MockQuestion(str(freq_max))
        elif "lower bound" in message_str.lower() or "min scrolls" in message_str.lower():
            return MockQuestion(str(freq_min))
        else:
            return MockQuestion(default or "10")
    
    def mock_select(message="", choices=None, **kwargs):
        message_str = str(message)
        if "export format" in message_str.lower():
            fmt_upper = export_format.upper()
            if fmt_upper in ["JSON", "CSV", "BOTH"]:
                return MockQuestion(fmt_upper)
        return MockQuestion(choices[0] if choices else None)
    
    def mock_confirm(message="", **kwargs):
        return MockQuestion(True)
    
    def mock_checkbox(message="", choices=None, **kwargs):
        return MockQuestion(choices if choices else [])
    
    questionary.text = mock_text
    questionary.select = mock_select
    questionary.confirm = mock_confirm
    questionary.checkbox = mock_checkbox
    
    # Run
    try:
        scraper.run(config, cookies)
    except Exception as e:
        print(f"Error during scraping: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore
        questionary.text = original_text
        questionary.select = original_select
        questionary.confirm = original_confirm
        questionary.checkbox = mock_checkbox  # best effort


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "RhenovattioDejaVu"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    fmt = sys.argv[3] if len(sys.argv) > 3 else "JSON"
    run_scraper(target, limit, fmt)
