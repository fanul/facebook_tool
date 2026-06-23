import os
import json
import csv
import time
import re
import random
import math
import sys
# Reconfigure stdout to UTF-8 to prevent encoding errors on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# Add project root directory to sys.path if run directly
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import urllib.parse as urlparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
import questionary

from core.base_tool import BaseTool
from core.engine import ScrapingEngine
from core.auth import check_facebook_login


console = Console()

class FacebookPostScraper(BaseTool):
    @property
    def id(self) -> str:
        return "fb_post_scraper"

    @property
    def name(self) -> str:
        return "Facebook Post Scraper"

    @property
    def description(self) -> str:
        return "Scrape posts from a Facebook profile or page using mbasic.facebook.com"

    def run(self, config: dict, cookies: dict) -> None:
        console.print(Panel("[bold blue]Facebook Post Scraper[/bold blue]\nScrape posts from Facebook profiles or pages.", border_style="blue"))

        if not cookies:
            console.print("[bold red]Error: No active Facebook cookies found. Please set cookies in the main menu first.[/bold red]")
            return

        # Prompt for target
        target_input = questionary.text(
            "Enter Facebook Username, Profile ID, Profile URL, or Group URL:",
            validate=lambda val: True if len(val.strip()) > 0 else "Target cannot be empty."
        ).ask()
        
        if not target_input:
            return

        target_input = target_input.strip()
        target = target_input
        target_type = "profile"   # 'profile' or 'group'

        # Parse target from URL if user entered a full URL
        if "facebook.com" in target_input:
            # Handle URLs like:
            # https://www.facebook.com/profile.php?id=10008323621
            # https://www.facebook.com/groups/mygroup/
            # https://www.facebook.com/groups/123456789/
            # https://mbasic.facebook.com/zuck
            # https://www.facebook.com/people/Some-Name/10008323621/
            if "profile.php" in target_input:
                parsed_url = urlparse.urlparse(target_input)
                queries = urlparse.parse_qs(parsed_url.query)
                if "id" in queries:
                    target = queries["id"][0]
            else:
                # Remove protocol, www., mbasic., etc.
                clean_path = target_input
                for prefix in ["https://", "http://", "www.", "mbasic.", "m.", "web."]:
                    if prefix in clean_path:
                        clean_path = clean_path.split(prefix, 1)[-1]
                # Now it should look like facebook.com/zuck or facebook.com/groups/name
                parts = clean_path.split("facebook.com/", 1)[-1].strip("/").split("/")

                if len(parts) >= 2 and parts[0] == "groups":
                    # Group URL: facebook.com/groups/{name_or_id}
                    target_type = "group"
                    target = parts[1].split("?")[0]   # strip any query params
                elif len(parts) >= 3 and parts[0] == "people":
                    # /people/Some-Name/12345 style
                    target = parts[2]
                elif len(parts) >= 1:
                    # Regular username: facebook.com/zuck
                    target = parts[0]
                    if "?" in target:
                        target = target.split("?", 1)[0]

        # If user typed just "groups/mygroup" or "groups/123" without full URL
        elif target_input.startswith("groups/"):
            target_type = "group"
            target = target_input.split("groups/", 1)[-1].strip("/").split("?")[0]

        # Show confirmation of what was detected
        type_label = "[bold cyan]Group[/bold cyan]" if target_type == "group" else "[bold cyan]Profile/Page[/bold cyan]"
        console.print(f"[dim]  Detected target type: {type_label} → identifier: [bold]{target}[/bold][/dim]")

        # Prompt for limit (supports -1 or negative for all posts)
        limit_str = questionary.text("Enter max number of posts to scrape (use -1 or negative for all posts):", default="10").ask()
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 10

        scrape_all = limit <= 0
        if scrape_all:
            display_limit = "All"
            slice_limit = 999999
        else:
            display_limit = str(limit)
            slice_limit = limit

        # Load scroll settings from config with defaults
        settings = config.get("settings", {})
        default_max_wait = settings.get("max_scroll_wait_seconds", 180)
        default_freq_min = settings.get("scroll_freq_min", 1)
        default_freq_max = settings.get("scroll_freq_max", 3)

        # Prompt for scroll settings
        max_wait_str = questionary.text(
            "Enter max anti-bot waiting time (seconds, 0 to disable wait):",
            default=str(default_max_wait)
        ).ask()
        try:
            max_scroll_wait = int(max_wait_str)
        except ValueError:
            max_scroll_wait = 180

        freq_min_str = questionary.text(
            "Enter scroll frequency lower bound (min scrolls before waiting):",
            default=str(default_freq_min)
        ).ask()
        try:
            scroll_freq_min = int(freq_min_str)
        except ValueError:
            scroll_freq_min = 1

        freq_max_str = questionary.text(
            "Enter scroll frequency upper bound (max scrolls before waiting):",
            default=str(default_freq_max)
        ).ask()
        try:
            scroll_freq_max = int(freq_max_str)
        except ValueError:
            scroll_freq_max = 3

        # Ensure scroll freq bounds are valid
        if scroll_freq_min < 1:
            scroll_freq_min = 1
        if scroll_freq_max < scroll_freq_min:
            scroll_freq_max = scroll_freq_min

        # Save settings back to config.json
        settings["max_scroll_wait_seconds"] = max_scroll_wait
        settings["scroll_freq_min"] = scroll_freq_min
        settings["scroll_freq_max"] = scroll_freq_max
        config["settings"] = settings
        try:
            with open("config.json", 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Prompt for export format
        export_format = questionary.select(
            "Select export format:",
            choices=["JSON", "CSV", "Both"]
        ).ask()

        # Build initial URL based on target type
        if target_type == "group":
            # Groups always use /groups/{name_or_id}
            base_url = f"https://www.facebook.com/groups/{target}"
        elif target.isdigit():
            base_url = f"https://www.facebook.com/profile.php?id={target}"
        else:
            base_url = f"https://www.facebook.com/{target}"

        engine = ScrapingEngine(config)
        graphql_data = []

        # Setup export directory and file paths
        export_dir = config.get("settings", {}).get("default_export_dir", "exports")
        os.makedirs(export_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = os.path.join(export_dir, f"fb_{target}_{timestamp}")
        
        json_file = f"{filename_base}.json"
        csv_file = f"{filename_base}.csv"
        
        # Initialize output files on disk immediately
        if export_format in ["CSV", "Both"]:
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Post ID", "Timestamp", "Content", "Story Link", "File Attached"])
                
        if export_format in ["JSON", "Both"]:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump([], f)

        scraped_posts = []
        written_post_ids = set()
        seen_contents = set()

        # Define all helper functions for parsing first, so scroll_action and later parsing can use them.
        def search_for_stories(data):
            stories = []
            if isinstance(data, dict):
                typename = data.get("__typename", "")

                # Profile/Page post nodes
                if typename in ["Story", "FeedUnit"] or "comet_sections" in data:
                    if "id" in data or "comet_sections" in data:
                        stories.append(data)

                # Group post nodes (GroupFeedUnit wraps the actual story)
                if typename in ["GroupFeedUnit", "CometGroupDiscussionRootSuccessQuery"]:
                    inner = data.get("story") or data.get("node")
                    if inner:
                        stories.append(inner)
                    else:
                        stories.append(data)

                # Profile timeline feed edges
                if "timeline_list_feed_units" in data:
                    units = data["timeline_list_feed_units"] or {}
                    for edge in units.get("edges", []):
                        node = edge.get("node")
                        if node:
                            stories.append(node)

                # Group feed edges (two possible key names Facebook uses)
                for group_feed_key in ["group_feed", "group_timeline_list_feed"]:
                    if group_feed_key in data:
                        feed = data[group_feed_key] or {}
                        for edge in feed.get("edges", []):
                            node = edge.get("node")
                            if node:
                                stories.append(node)

                # Pinned post on profiles
                if "profile_pinned_post" in data:
                    pinned = data["profile_pinned_post"] or {}
                    node = pinned.get("pinned_post_story")
                    if node:
                        stories.append(node)

                for k, v in data.items():
                    stories.extend(search_for_stories(v))
            elif isinstance(data, list):
                for item in data:
                    stories.extend(search_for_stories(item))
            return stories

        def find_field_value(d, field_name):
            if isinstance(d, dict):
                if field_name in d:
                    return d[field_name]
                for k, v in d.items():
                    res = find_field_value(v, field_name)
                    if res is not None:
                        return res
            elif isinstance(d, list):
                for item in d:
                    res = find_field_value(item, field_name)
                    if res is not None:
                        return res
            return None

        def find_images(d):
            imgs = []
            if isinstance(d, dict):
                if "photo_image" in d and isinstance(d["photo_image"], dict) and "uri" in d["photo_image"]:
                    imgs.append(d["photo_image"]["uri"])
                if "media" in d and isinstance(d["media"], dict):
                    media_obj = d["media"]
                    if "image" in media_obj and isinstance(media_obj["image"], dict) and "uri" in media_obj["image"]:
                        imgs.append(media_obj["image"]["uri"])
                    if "photo_image" in media_obj and isinstance(media_obj["photo_image"], dict) and "uri" in media_obj["photo_image"]:
                        imgs.append(media_obj["photo_image"]["uri"])
                for k, v in d.items():
                    if k not in ["profile_picture", "actors", "actor_photo"]:
                        imgs.extend(find_images(v))
            elif isinstance(d, list):
                for item in d:
                    imgs.extend(find_images(item))
            return imgs

        def find_valid_url(d):
            if isinstance(d, dict):
                if "url" in d and d["url"] and any(p in d["url"] for p in ["posts", "permalink", "photo", "story"]):
                    return d["url"]
                for k, v in d.items():
                    res = find_valid_url(v)
                    if res:
                        return res
            elif isinstance(d, list):
                for item in d:
                    res = find_valid_url(item)
                    if res:
                        return res
            return ""

        def export_new_posts(html, g_data):
            json_blobs = []
            if html:
                all_script_blocks = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html)
                for sb in all_script_blocks:
                    try:
                        json_blobs.append(json.loads(sb))
                    except Exception:
                        pass
            for g_text in g_data:
                for line in g_text.strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        json_blobs.append(json.loads(line))
                    except Exception:
                        pass
            
            # Clear raw string packets from memory immediately to save RAM
            g_data.clear()
            
            all_story_nodes = []
            for blob in json_blobs:
                all_story_nodes.extend(search_for_stories(blob))
                
            extracted_posts = {}
            for node in all_story_nodes:
                comet_sections = node.get("comet_sections", {})
                if not comet_sections and isinstance(node, dict):
                    story = node.get("story")
                    if isinstance(story, dict):
                        node = story
                        comet_sections = node.get("comet_sections", {})
                
                message_text = ""
                if comet_sections:
                    msg_obj = find_field_value(comet_sections, "message")
                    if isinstance(msg_obj, dict):
                        message_text = msg_obj.get("text") or ""
                if not message_text:
                    msg_obj = find_field_value(node, "message")
                    if isinstance(msg_obj, dict):
                        message_text = msg_obj.get("text") or ""
                
                images = list(set([img for img in find_images(node) if img]))
                if not message_text and not images:
                    continue
                    
                url = ""
                if comet_sections:
                    url = find_field_value(comet_sections, "url")
                    if url and not any(p in url for p in ["posts", "permalink", "photo", "story"]):
                        url = ""
                if not url:
                    url = find_valid_url(node)

                post_id = node.get("id") or ""
                url_post_id = ""
                if url:
                    if "/posts/" in url:
                        url_post_id = url.split("/posts/")[-1].split("?")[0].strip("/")
                    elif "fbid=" in url:
                        parsed = urlparse.urlparse(url)
                        queries = urlparse.parse_qs(parsed.query)
                        if "fbid" in queries:
                            url_post_id = queries["fbid"][0]
                    elif "/photos/" in url:
                        parts = url.split("/photos/")[-1].split("/")
                        if len(parts) >= 2:
                            url_post_id = parts[1]
                            
                dedup_key = url_post_id or post_id
                if not dedup_key:
                    continue
                
                if dedup_key not in extracted_posts:
                    extracted_posts[dedup_key] = {
                        "post_id": url_post_id or post_id,
                        "content": message_text,
                        "timestamp": find_field_value(comet_sections, "creation_time") or find_field_value(node, "creation_time"),
                        "story_link": url if url.startswith("http") else f"https://www.facebook.com{url}" if url else "",
                        "images": images,
                        "scraped_at": datetime.now().isoformat()
                    }
                else:
                    existing = extracted_posts[dedup_key]
                    if not existing["content"] and message_text:
                        existing["content"] = message_text
                    if not existing["timestamp"]:
                        existing["timestamp"] = find_field_value(comet_sections, "creation_time") or find_field_value(node, "creation_time")
                    if not existing["story_link"] and url:
                        existing["story_link"] = url if url.startswith("http") else f"https://www.facebook.com{url}" if url else ""
                    if len(images) > len(existing["images"]):
                        existing["images"] = images

            new_count = 0
            sorted_keys = sorted(
                extracted_posts.keys(),
                key=lambda k: (
                    1 if extracted_posts[k]["story_link"] else 0,
                    1 if extracted_posts[k]["timestamp"] else 0,
                    0 if "Uzpf" in k else 1
                ),
                reverse=True
            )
            
            for k in sorted_keys:
                p = extracted_posts[k]
                content_slug = p["content"].strip()
                content_slug = re.sub(r'\s+', ' ', content_slug)
                
                is_duplicate = False
                if p["post_id"] in written_post_ids:
                    is_duplicate = True
                elif content_slug and content_slug in seen_contents:
                    is_duplicate = True
                    
                if not is_duplicate:
                    if not scrape_all and len(scraped_posts) >= slice_limit:
                        break
                        
                    written_post_ids.add(p["post_id"])
                    if content_slug:
                        seen_contents.add(content_slug)
                    
                    ts = p["timestamp"]
                    if ts:
                        try:
                            p["timestamp"] = datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S')
                        except Exception:
                            p["timestamp"] = str(ts)
                    else:
                        p["timestamp"] = "Unknown"
                        
                    scraped_posts.append(p)
                    new_count += 1
                    
                    if export_format in ["CSV", "Both"]:
                        with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                            writer = csv.writer(f)
                            writer.writerow([
                                p["post_id"],
                                p["timestamp"],
                                p["content"],
                                p["story_link"],
                                ", ".join(p["images"])
                            ])
                            
                    if export_format in ["JSON", "Both"]:
                        with open(json_file, 'w', encoding='utf-8') as f:
                            json.dump(scraped_posts, f, indent=2, ensure_ascii=False)
            
            if new_count > 0:
                console.print(f"[green]✓ Exported {new_count} new posts to disk. (Total: {len(scraped_posts)})[/green]")
            return new_count

        # ── Health monitoring thresholds ─────────────────────────────────────
        # Consecutive scrolls with 0 new posts before escalating warning
        STALL_WARN_THRESHOLD    = 5
        # Consecutive scrolls with 0 new posts before triggering session check
        STALL_CHECK_THRESHOLD   = 10
        # Consecutive scrolls with 0 new posts before auto-stopping
        STALL_AUTOSTOP_THRESHOLD = 20
        # How often (scrolls) to do a periodic session ping even if not stalled
        SESSION_PING_EVERY      = 50

        # Initialize progress and task as None to avoid linter unbound warning
        progress = None
        task = None

        def do_session_check() -> tuple[bool, str]:
            """Lightweight session health check via mbasic (runs in background thread-safe via requests)."""
            try:
                ua = settings.get("user_agent")
                ok, msg = check_facebook_login(cookies, ua)
                return ok, msg
            except Exception as e:
                return False, str(e)

        def scroll_action(page):
            nonlocal cookies  # needed for session re-check

            # ── Stall / health tracking state ─────────────────────────────
            consecutive_stall   = 0   # scrolls in a row with 0 new posts
            consecutive_no_pkt  = 0   # scrolls in a row with 0 new packets
            last_new_post_at    = 0   # scroll number when last post was found
            session_ok          = True
            last_session_check  = 0   # scroll number of last session check
            last_session_msg    = "Not checked yet"
            packets_this_scroll = 0

            # Intercept response
            def on_response(response):
                nonlocal packets_this_scroll
                if "graphql" in response.url:
                    try:
                        text = response.text()
                        # Profile/page feed keywords
                        profile_keywords = [
                            "timeline_list_feed_units", "feedUnit", "comet_sections"
                        ]
                        # Group feed keywords
                        group_keywords = [
                            "GroupFeedUnitEdge", "group_feed", "NodeGroupTimeline",
                            "CometGroupDiscussionRootSuccessQuery", "group_timeline_list_feed"
                        ]
                        all_keywords = profile_keywords + group_keywords
                        if any(k in text for k in all_keywords):
                            graphql_data.append(text)
                            packets_this_scroll += 1
                            console.print(f"[bold green]✓[/bold green] [cyan]Captured GraphQL packet ({len(text):,} bytes)[/cyan]")
                    except Exception:
                        pass
            page.on("response", on_response)

            # Wait for feed to load initially
            console.print("[cyan]Waiting for initial feed elements to render...[/cyan]")
            if progress and task:
                progress.update(task, description="[cyan]Waiting for initial feed elements...[/cyan]")
            try:
                page.wait_for_selector('div[data-ad-preview="message"]', timeout=12000)
            except Exception:
                page.wait_for_timeout(3000)

            last_height = page.evaluate("document.body.scrollHeight")
            scroll_count = 0
            
            # Estimate required scrolls based on limit
            if scrape_all:
                target_scrolls = 9999
            else:
                target_scrolls = math.ceil(slice_limit / 5)
                target_scrolls = max(1, target_scrolls)
            
            console.print(f"[green]Initial page loaded successfully. Starting dynamic scroll process.[/green]")
            console.print(f"[dim]Target scrolls: ~{target_scrolls if not scrape_all else 'Unlimited'} (stops early if height doesn't increase or limit met)[/dim]\n")

            # Determine the first scroll frequency wait target
            scrolls_to_next_wait = random.randint(scroll_freq_min, scroll_freq_max)
            next_wait_scroll = scroll_count + scrolls_to_next_wait
            console.print(f"[dim]Anti-bot plan: Will perform randomized pause after {scrolls_to_next_wait} scrolls (Scroll #{next_wait_scroll})[/dim]")

            while scroll_count < target_scrolls:
                scroll_count += 1
                packets_this_scroll = 0   # reset per-scroll packet counter

                console.print(f"[bold blue]>>> Scroll #{scroll_count} of ~{target_scrolls if not scrape_all else 'Unlimited'}[/bold blue]")
                console.print(f"[cyan]  ↓ Scrolling to bottom... (Height: {last_height:,}px)[/cyan]")
                if progress and task:
                    progress.update(task, description=f"[cyan]Scrolling #{scroll_count} | Posts: {len(scraped_posts)} | Stall: {consecutive_stall}[/cyan]")
                
                # Perform scroll
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                
                # Wait for content/height update
                if progress and task:
                    progress.update(task, description=f"[cyan]Fetching network response (Scroll #{scroll_count})...[/cyan]")
                page.wait_for_timeout(4000)
                
                new_height = page.evaluate("document.body.scrollHeight")
                
                # Height-unchanged nudge
                if new_height == last_height:
                    console.print(f"[yellow]  ⚠ Height unchanged ({new_height:,}px). Nudging scroll...[/yellow]")
                    if progress and task:
                        progress.update(task, description="[yellow]Nudging scroll to trigger lazy load...[/yellow]")
                    page.evaluate("window.scrollBy(0, -600)")
                    page.wait_for_timeout(1500)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(4000)
                    new_height = page.evaluate("document.body.scrollHeight")
                
                console.print(f"[dim]  Height: {last_height:,}px → {new_height:,}px | Packets this scroll: {packets_this_scroll}[/dim]")
                
                # Auto-stop: page height didn't grow at all
                if new_height == last_height:
                    console.print("[bold green]  ✓ Reached the end of the feed. Stopping.[/bold green]")
                    break
                
                last_height = new_height
                
                # ── Export posts & track stall status ───────────────────────
                current_html = page.content()
                new_found = export_new_posts(current_html, graphql_data)
                current_count = len(scraped_posts)

                if new_found > 0:
                    consecutive_stall  = 0
                    consecutive_no_pkt = 0
                    last_new_post_at   = scroll_count
                else:
                    consecutive_stall += 1
                    if packets_this_scroll == 0:
                        consecutive_no_pkt += 1
                    else:
                        consecutive_no_pkt = 0  # got packets but no new deduped posts — normal

                # ── Periodic session ping (every N scrolls, even if healthy) ─
                if scroll_count - last_session_check >= SESSION_PING_EVERY:
                    console.print(f"[dim]  🔄 Periodic session check (every {SESSION_PING_EVERY} scrolls)...[/dim]")
                    session_ok, last_session_msg = do_session_check()
                    last_session_check = scroll_count
                    if session_ok:
                        console.print(f"[dim]  ✅ Session OK: {last_session_msg}[/dim]")
                    else:
                        console.print(f"[bold red]  ❌ Session INVALID: {last_session_msg}[/bold red]")

                # ── Status dashboard line ────────────────────────────────────
                stall_color  = "green" if consecutive_stall == 0 else ("yellow" if consecutive_stall < STALL_CHECK_THRESHOLD else "red")
                session_icon = "✅" if session_ok else "❌"
                since_last   = (f"Scroll #{last_new_post_at}" if last_new_post_at else "none yet")
                console.print(
                    f"[magenta]  📊 Posts: {current_count}/{display_limit} "
                    f"| Height: {new_height:,}px "
                    f"| Pkts: {packets_this_scroll} "
                    f"| [{stall_color}]Stall: {consecutive_stall}[/{stall_color}] "
                    f"| Last new: {since_last} "
                    f"| Session: {session_icon}[/magenta]"
                )

                # ── Stall escalation logic ───────────────────────────────────
                if consecutive_stall >= STALL_WARN_THRESHOLD and consecutive_stall < STALL_CHECK_THRESHOLD:
                    console.print(
                        f"[yellow]  ⚠ STALL WARNING: No new posts for {consecutive_stall} consecutive scrolls. "
                        f"Last new post at Scroll #{last_new_post_at}.[/yellow]"
                    )

                elif consecutive_stall == STALL_CHECK_THRESHOLD:
                    console.print(
                        f"[bold yellow]  ⚠ STALL: {consecutive_stall} scrolls with no new posts. "
                        f"Triggering session health check...[/bold yellow]"
                    )
                    session_ok, last_session_msg = do_session_check()
                    last_session_check = scroll_count
                    if session_ok:
                        console.print(
                            f"[green]  ✅ Session still valid: {last_session_msg}[/green]\n"
                            f"[dim]  → Data may be exhausted or Facebook is throttling. Continuing...[/dim]"
                        )
                    else:
                        console.print(
                            f"[bold red]  ❌ SESSION EXPIRED: {last_session_msg}[/bold red]\n"
                            f"[bold red]  → Stopping scraper. Please refresh your cookies and restart.[/bold red]"
                        )
                        break

                elif consecutive_stall >= STALL_AUTOSTOP_THRESHOLD:
                    # Do one final session check before stopping
                    session_ok, last_session_msg = do_session_check()
                    last_session_check = scroll_count
                    status_str = f"Session: {'✅ valid' if session_ok else '❌ EXPIRED'} — {last_session_msg}"
                    console.print(
                        f"[bold red]  🛑 AUTO-STOP: {consecutive_stall} scrolls with zero new posts.\n"
                        f"  {status_str}\n"
                        f"  Last new post was at Scroll #{last_new_post_at}. "
                        f"Likely reached the end of available posts or session is rate-limited.[/bold red]"
                    )
                    break

                # ── Limit check ──────────────────────────────────────────────
                if not scrape_all and current_count >= slice_limit:
                    console.print(f"[bold green]  ✓ Target limit ({slice_limit}) reached ({current_count} posts). Stopping.[/bold green]")
                    break
                
                if scroll_count >= target_scrolls:
                    break
                    
                # ── Anti-bot pause ───────────────────────────────────────────
                if scroll_count == next_wait_scroll:
                    scrolls_to_next_wait = random.randint(scroll_freq_min, scroll_freq_max)
                    next_wait_scroll = scroll_count + scrolls_to_next_wait
                    
                    if max_scroll_wait > 0:
                        delay = random.randint(0, max_scroll_wait)
                        if delay > 0:
                            console.print(f"[yellow]  ⏱ Anti-bot pause: {delay}s (randomized 0-{max_scroll_wait}s)[/yellow]")
                            console.print(f"[dim]  Next pause after {scrolls_to_next_wait} scrolls (Scroll #{next_wait_scroll})[/dim]")
                            
                            for elapsed in range(1, delay + 1):
                                remaining = delay - elapsed
                                console.print(
                                    f"[yellow]   ⏳ {elapsed:>3}s elapsed | {remaining:>3}s remaining[/yellow]",
                                    end="\r"
                                )
                                if progress and task:
                                    progress.update(
                                        task,
                                        description=f"[yellow]⏱ Anti-bot pause: {elapsed}s/{delay}s | Posts: {len(scraped_posts)} | Stall: {consecutive_stall}[/yellow]"
                                    )
                                time.sleep(1)
                            console.print()  # newline after countdown
                            
                            if progress and task:
                                progress.update(task, description="[green]✓ Pause finished. Resuming...[/green]")
                            console.print("[green]  ✓ Pause done. Resuming scroll...[/green]\n")
                        else:
                            console.print("[dim]  ⏱ 0s delay selected. Resuming immediately.[/dim]")
                            console.print(f"[dim]  Next pause after {scrolls_to_next_wait} scrolls (Scroll #{next_wait_scroll})[/dim]\n")
                    else:
                        console.print("[dim]  ⏱ Anti-bot pauses disabled.[/dim]\n")
                else:
                    scrolls_left = next_wait_scroll - scroll_count
                    console.print(f"[dim]  Next pause in {scrolls_left} scrolls (Scroll #{next_wait_scroll})[/dim]\n")

        posts = []
        console.print(f"\n[yellow]Starting headless browser session for target: [bold]{target}[/bold]...[/yellow]")

        # Progress bar/spinner for the browser loading phase
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Scrolling and capturing feed data...", total=None)
            
            try:
                selector = engine.fetch_page(base_url, page_action=scroll_action)
            except Exception as e:
                console.print(f"\n[bold red]Error fetching page: {e}[/bold red]")
                return

        # Inspect if session is valid or got redirected
        if "login" in selector.url or "login.php" in selector.url:
            console.print("\n[bold red]Session expired or blocked by Facebook. Redirected to login page.[/bold red]")
            return

        # Parse captured json data
        console.print("[cyan]Performing final disk export and memory cleanup...[/cyan]")
        final_html = selector.html_content if hasattr(selector, "html_content") else ""
        export_new_posts(final_html, graphql_data)

        posts = scraped_posts

        # Output results
        if not posts:
            console.print("\n[bold yellow]No posts scraped. Possible reasons:[/bold yellow]")
            console.print("1. Your Facebook cookies are invalid or expired.")
            console.print("2. The profile/page is private or restricts automated access.")
            console.print("3. There is no public content available for this account.")
            return

        console.print(f"\n[green]Scraped [bold]{len(posts)}[/bold] posts successfully![/green]")
        
        if export_format in ["JSON", "Both"]:
            console.print(f"Saved JSON export: [bold underline]{os.path.abspath(json_file)}[/bold underline]")
            
        if export_format in ["CSV", "Both"]:
            console.print(f"Saved CSV export: [bold underline]{os.path.abspath(csv_file)}[/bold underline]")

        # Print preview table
        table = Table(title="Scraped Posts Preview", show_header=True, header_style="bold magenta")
        table.add_column("Post ID", style="dim", width=15)
        table.add_column("Timestamp", width=15)
        table.add_column("Content Preview", width=50)
        
        for post in posts[:5]:
            preview = post["content"][:47] + "..." if len(post["content"]) > 50 else post["content"]
            table.add_row(post["post_id"], post["timestamp"], preview)
            
        console.print(table)

if __name__ == "__main__":
    console.print("\n[bold yellow]⚠ Warning: This is a plugin module for the modular web scraper suite.[/bold yellow]")
    console.print("To run the scraper tool, please execute the main entry point from the root folder instead:")
    console.print("  [bold green]python main.py[/bold green]\n")

