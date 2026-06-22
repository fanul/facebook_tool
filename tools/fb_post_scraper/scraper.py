import os
import json
import csv
import time
import re
import random
import math
import sys
import urllib.parse as urlparse
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
import questionary

from core.base_tool import BaseTool
from core.engine import ScrapingEngine

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
            "Enter Facebook Username, Profile ID, or Profile URL:",
            validate=lambda val: True if len(val.strip()) > 0 else "Target cannot be empty."
        ).ask()
        
        if not target_input:
            return

        target_input = target_input.strip()
        target = target_input

        # Parse target from URL if user entered a full URL
        if "facebook.com" in target_input:
            # Handle URLs like:
            # https://www.facebook.com/profile.php?id=10008323621
            # https://mbasic.facebook.com/zuck
            # https://facebook.com/groups/name/
            # https://www.facebook.com/people/Some-Name/10008323621/
            if "profile.php" in target_input:
                # Extract id query param
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
                # Now it should look like facebook.com/zuck or facebook.com/profile/name/1234
                parts = clean_path.split("facebook.com/", 1)[-1].strip("/").split("/")
                
                # Check for /people/Name/ID style
                if len(parts) >= 3 and parts[0] == "people":
                    target = parts[2]
                elif len(parts) >= 1:
                    # Take the first folder level as target username (e.g. zuck)
                    target = parts[0]
                    # Clean query parameters if any (e.g. zuck?refid=...)
                    if "?" in target:
                        target = target.split("?", 1)[0]

        # Prompt for limit
        limit_str = questionary.text("Enter max number of posts to scrape:", default="10").ask()
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 10

        # Prompt for export format
        export_format = questionary.select(
            "Select export format:",
            choices=["JSON", "CSV", "Both"]
        ).ask()

        # Build initial URL (Desktop version)
        if target.isdigit():
            base_url = f"https://www.facebook.com/profile.php?id={target}"
        else:
            base_url = f"https://www.facebook.com/{target}"

        engine = ScrapingEngine(config)
        graphql_data = []

        # Define all helper functions for parsing first, so scroll_action and later parsing can use them.
        def search_for_stories(data):
            stories = []
            if isinstance(data, dict):
                typename = data.get("__typename")
                if typename in ["Story", "FeedUnit"] or "comet_sections" in data:
                    if "id" in data or "comet_sections" in data:
                        stories.append(data)
                if "timeline_list_feed_units" in data:
                    units = data["timeline_list_feed_units"] or {}
                    for edge in units.get("edges", []):
                        node = edge.get("node")
                        if node:
                            stories.append(node)
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

        def count_captured_posts(html, g_data):
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
                
                images = find_images(node)
                if not message_text and not images:
                    continue
                    
                url = ""
                if comet_sections:
                    url = find_field_value(comet_sections, "url")
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
                extracted_posts[dedup_key] = True
            return len(extracted_posts)

        # Initialize progress and task as None to avoid linter unbound warning
        progress = None
        task = None

        def scroll_action(page):
            # Intercept response
            def on_response(response):
                if "graphql" in response.url:
                    try:
                        text = response.text()
                        if "timeline_list_feed_units" in text or "feedUnit" in text or "comet_sections" in text:
                            graphql_data.append(text)
                            # Print detailed CLI output when fetching data
                            console.print(f"[bold green]✓[/bold green] [cyan]Captured GraphQL packet ({len(text)} bytes) from Facebook.[/cyan]")
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
            target_scrolls = math.ceil(limit / 5)
            target_scrolls = max(1, target_scrolls)
            
            console.print(f"[green]Initial page loaded successfully. Starting dynamic scroll process.[/green]")
            console.print(f"[dim]Target scrolls: ~{target_scrolls} (stops early if height doesn't increase)[/dim]\n")

            while scroll_count < target_scrolls:
                scroll_count += 1
                console.print(f"[bold blue]>>> Scroll #{scroll_count} of ~{target_scrolls}[/bold blue]")
                console.print(f"[cyan]Scrolling to bottom... (Current Height: {last_height}px)[/cyan]")
                if progress and task:
                    progress.update(task, description=f"[cyan]Scrolling #{scroll_count} (Height: {last_height}px)...[/cyan]")
                
                # Perform scroll
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                
                # Wait 4 seconds for content/height update to start and fetch any lazy-loaded content
                if progress and task:
                    progress.update(task, description=f"[cyan]Fetching content/waiting for network (Scroll #{scroll_count})...[/cyan]")
                page.wait_for_timeout(4000)
                
                new_height = page.evaluate("document.body.scrollHeight")
                
                # Verify if height actually increased
                if new_height == last_height:
                    # Let's perform a scroll nudge: scroll up slightly and then down again, wait to be sure
                    console.print(f"[yellow]⚠ Height unchanged ({new_height}px). Nudging scroll to trigger lazy load...[/yellow]")
                    if progress and task:
                        progress.update(task, description="[yellow]Nudging scroll to trigger lazy load...[/yellow]")
                    
                    page.evaluate("window.scrollBy(0, -600)")
                    page.wait_for_timeout(1500)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(4000)
                    
                    new_height = page.evaluate("document.body.scrollHeight")
                
                console.print(f"[dim]Page height position: {last_height}px -> {new_height}px[/dim]")
                
                # If height did not increase even after nudge, stop automatically
                if new_height == last_height:
                    console.print("[bold green]Reached the end of the feed (bottom of page). Stopping scroll.[/bold green]")
                    break
                
                # Update position tracker
                last_height = new_height
                
                # Show Position Reminder
                current_html = page.content()
                current_count = count_captured_posts(current_html, graphql_data)
                console.print(f"[magenta]ℹ Position Reminder: Height = {new_height}px | Collected Packets = {len(graphql_data)} | Approx. Unique Posts = {current_count}/{limit}[/magenta]")
                
                # If we have reached or exceeded the required limit, we can stop early!
                if current_count >= limit:
                    console.print(f"[bold green]✓ Target post limit ({limit}) reached/exceeded ({current_count} posts). Stopping scroll.[/bold green]")
                    break
                
                if scroll_count >= target_scrolls:
                    break
                    
                # Delay between 60 and 180 seconds (1-3 minutes)
                delay = random.randint(60, 180)
                console.print(f"[yellow]⏱ Anti-bot pause: Next scroll queued in {delay} seconds (randomized 1-3 minutes)...[/yellow]")
                
                # Countdown display using rich.progress task description
                for remaining in range(delay, 0, -1):
                    mins, secs = divmod(remaining, 60)
                    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    if progress and task:
                        progress.update(task, description=f"[yellow]⏱ Waiting: {time_str} remaining...[/yellow]")
                    time.sleep(1)
                
                if progress and task:
                    progress.update(task, description="[green]✓ Pause finished. Resuming...[/green]")
                console.print("[green]✓ Pause finished. Resuming scroll...[/green]\n")

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
        console.print("[cyan]Parsing preloaded JSON blocks and GraphQL data...[/cyan]")
        initial_html = selector.html_content if hasattr(selector, "html_content") else ""
        all_script_blocks = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', initial_html)
        
        json_blobs = []
        for sb in all_script_blocks:
            try:
                json_blobs.append(json.loads(sb))
            except Exception:
                pass
                
        for g_text in graphql_data:
            for line in g_text.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    json_blobs.append(json.loads(line))
                except Exception:
                    pass

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
            
            creation_time = None
            if comet_sections:
                creation_time = find_field_value(comet_sections, "creation_time")
            if creation_time is None:
                creation_time = find_field_value(node, "creation_time")
                
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
                
            images = list(set([img for img in find_images(node) if img]))
            
            if not message_text and not images:
                continue
                
            if dedup_key not in extracted_posts:
                extracted_posts[dedup_key] = {
                    "post_id": url_post_id or post_id,
                    "content": message_text,
                    "timestamp": creation_time,
                    "story_link": url if url.startswith("http") else f"https://www.facebook.com{url}" if url else "",
                    "images": images,
                    "scraped_at": datetime.now().isoformat()
                }
            else:
                existing = extracted_posts[dedup_key]
                if not existing["content"] and message_text:
                    existing["content"] = message_text
                if not existing["timestamp"] and creation_time:
                    existing["timestamp"] = creation_time
                if not existing["story_link"] and url:
                    existing["story_link"] = url if url.startswith("http") else f"https://www.facebook.com{url}" if url else ""
                if len(images) > len(existing["images"]):
                    existing["images"] = images

        # Deduplicate further by content to collapse duplicates
        final_posts = []
        seen_contents = set()
        
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
            
            if content_slug and content_slug in seen_contents:
                for fp in final_posts:
                    fp_slug = re.sub(r'\s+', ' ', fp["content"].strip())
                    if fp_slug == content_slug:
                        if not fp["timestamp"] and p["timestamp"]:
                            fp["timestamp"] = p["timestamp"]
                        if not fp["story_link"] and p["story_link"]:
                            fp["story_link"] = p["story_link"]
                        if len(p["images"]) > len(fp["images"]):
                            fp["images"] = p["images"]
                        break
                continue
                
            if content_slug:
                seen_contents.add(content_slug)
            final_posts.append(p)

        # Slice to requested limit
        final_posts = final_posts[:limit]

        # Convert timestamps for presentation
        for p in final_posts:
            ts = p["timestamp"]
            if ts:
                try:
                    p["timestamp"] = datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    p["timestamp"] = str(ts)
            else:
                p["timestamp"] = "Unknown"

        posts = final_posts

        # Output results
        if not posts:
            console.print("\n[bold yellow]No posts scraped. Possible reasons:[/bold yellow]")
            console.print("1. Your Facebook cookies are invalid or expired.")
            console.print("2. The profile/page is private or restricts automated access.")
            console.print("3. There is no public content available for this account.")
            return

        console.print(f"\n[green]Scraped [bold]{len(posts)}[/bold] posts successfully![/green]")
        
        # Save to file
        export_dir = config.get("settings", {}).get("default_export_dir", "exports")
        os.makedirs(export_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = os.path.join(export_dir, f"fb_{target}_{timestamp}")
        
        # Create full output path links for reporting
        json_file = f"{filename_base}.json"
        csv_file = f"{filename_base}.csv"
        
        if export_format in ["JSON", "Both"]:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(posts, f, indent=2, ensure_ascii=False)
            console.print(f"Saved JSON export: [bold underline]{os.path.abspath(json_file)}[/bold underline]")
            
        if export_format in ["CSV", "Both"]:
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Post ID", "Timestamp", "Content", "Story Link", "File Attached"])
                for post in posts:
                    writer.writerow([
                        post["post_id"],
                        post["timestamp"],
                        post["content"],
                        post["story_link"],
                        ", ".join(post["images"])
                    ])
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
