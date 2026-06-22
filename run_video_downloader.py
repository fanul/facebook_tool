#!/usr/bin/env python3
"""
Non-interactive Facebook video/reels downloader wrapper.
Usage: python3 run_video_downloader.py <username_or_id> <limit> [quality] [sections]
Example: python3 run_video_downloader.py RhenovattioDejaVu 10 HD "Videos,Reels"
"""
import sys
import os
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.auth import parse_cookie_string, check_facebook_login
from tools.fb_video_downloader.scraper import FacebookVideoDownloader


def run_video_downloader(target, limit=10, quality="HD", sections="Videos,Reels", cookies_file="cookies.json", config_file="config.json"):
    """Run video downloader non-interactively."""
    
    # Load config
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Load cookies
    with open(cookies_file, 'r') as f:
        raw = f.read()
    cookies = parse_cookie_string(raw)
    config['active_cookies'] = cookies
    
    # Validate cookies
    ua = config.get('settings', {}).get('user_agent', '')
    is_ok, msg = check_facebook_login(cookies, ua)
    if not is_ok:
        print(f"Cookie validation failed: {msg}")
        return
    
    print(f"Cookie validated: {msg}")
    
    # Create downloader instance
    downloader = FacebookVideoDownloader()
    
    # Mock questionary for non-interactive mode
    import questionary
    original_text = questionary.text
    original_select = questionary.select
    original_checkbox = questionary.checkbox
    
    call_count = 0
    
    def mock_text(prompt="", default="", validate=lambda v: True):
        nonlocal call_count
        call_count += 1
        prompt_str = str(prompt)
        
        if "Facebook Username" in prompt_str or "Profile ID" in prompt_str or "Profile URL" in prompt_str:
            return target
        elif "Max videos" in prompt_str or "number" in prompt_str.lower():
            return str(limit)
        else:
            return default or str(limit)
    
    def mock_select(prompt="", choices=None):
        prompt_str = str(prompt)
        if "video quality" in prompt_str.lower() or "Preferred" in prompt_str:
            return "HD (fallback SD)" if quality.upper() == "HD" else "SD only"
        return choices[0] if choices else None
    
    def mock_checkbox(prompt="", choices=None):
        sections_list = [s.strip() for s in sections.split(",")]
        # Return only selected sections
        if choices:
            return [c for c in choices if c in sections_list]
        return ["Videos"]
    
    questionary.text = mock_text
    questionary.select = mock_select
    questionary.checkbox = mock_checkbox
    
    # Run
    try:
        downloader.run(config, cookies)
    except Exception as e:
        print(f"Error during video download: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore
        questionary.text = original_text
        questionary.select = original_select
        questionary.checkbox = original_checkbox


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "RhenovattioDejaVu"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    quality = sys.argv[3] if len(sys.argv) > 3 else "HD"
    sections = sys.argv[4] if len(sys.argv) > 4 else "Videos,Reels"
    run_video_downloader(target, limit, quality, sections)
