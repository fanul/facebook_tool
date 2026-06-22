import os
import json
import csv
import time
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
                import urllib.parse as urlparse
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

        # Build initial URL
        if target.isdigit():
            base_url = f"https://mbasic.facebook.com/profile.php?id={target}"
        else:
            base_url = f"https://mbasic.facebook.com/{target}"

        engine = ScrapingEngine(config)
        delay = engine.get_delay()

        posts = []
        current_url = base_url
        page_num = 1

        console.print(f"\n[yellow]Starting scrape for target: [bold]{target}[/bold]...[/yellow]")

        # Progress bar
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Scraping posts...", total=limit)

            while current_url and len(posts) < limit:
                progress.update(task, description=f"[cyan]Scraping page {page_num}...")
                
                try:
                    response = engine.fetch_page(current_url)
                except Exception as e:
                    console.print(f"\n[red]Error fetching page {page_num}: {e}[/red]")
                    break

                # Inspect if session is valid or got redirected
                if "login" in response.url or "login.php" in response.url:
                    console.print("\n[bold red]Session expired or blocked by Facebook. Redirected to login page.[/bold red]")
                    break

                # Extract posts on this page
                # In mbasic, posts are inside elements with data-ft attribute
                post_elements = response.css('*[data-ft]')
                
                if not post_elements:
                    console.print(f"\n[yellow]No posts found on page {page_num}. Ending search.[/yellow]")
                    break

                new_posts_found = 0
                for post_el in post_elements:
                    if len(posts) >= limit:
                        break

                    # Get data-ft attribute
                    data_ft_str = post_el.css('::attr(data-ft)').get()
                    post_id = None
                    if data_ft_str:
                        try:
                            data_ft = json.loads(data_ft_str)
                            post_id = (
                                data_ft.get('top_level_post_id') or 
                                data_ft.get('mf_story_key') or 
                                data_ft.get('story_fbid')
                            )
                        except Exception:
                            pass

                    # If no valid post_id was parsed, it might not be a top-level post container
                    if not post_id:
                        continue

                    # Deduplicate by post_id
                    if any(p['post_id'] == str(post_id) for p in posts):
                        continue

                    # Extract content text
                    # Look for paragraph elements inside the post
                    p_texts = post_el.css('p::text').getall()
                    
                    # Fallback to span elements if no p elements
                    if not p_texts:
                        # Grab all span text that isn't short or navigational
                        spans = post_el.css('span::text').getall()
                        p_texts = [s for s in spans if len(s.strip()) > 3]

                    content = " ".join([t.strip() for t in p_texts if t.strip()])
                    
                    # Extract links (story links)
                    links = post_el.css('a::attr(href)').getall()
                    story_link = ""
                    for href in links:
                        if 'story.php' in href or 'permalink.php' in href or '/posts/' in href or 'photo.php' in href:
                            if href.startswith('/'):
                                story_link = f"https://mbasic.facebook.com{href}"
                            else:
                                story_link = href
                            break
                            
                    # Extract images
                    images = post_el.css('img::attr(src)').getall()
                    post_images = []
                    for img in images:
                        # Exclude icons and generic tracking pixels
                        if 'static.xx' not in img and 'rsrc.php' not in img and img.startswith('http'):
                            post_images.append(img)
                            
                    # Extract post time
                    timestamp_text = post_el.css('abbr::text').get() or ""

                    # Skip empty posts
                    if not content and not post_images:
                        continue

                    posts.append({
                        "post_id": str(post_id),
                        "content": content,
                        "timestamp": timestamp_text,
                        "story_link": story_link,
                        "images": post_images,
                        "scraped_at": datetime.now().isoformat()
                    })
                    new_posts_found += 1
                    progress.update(task, advance=1)

                # Find pagination link
                next_url = None
                for a in response.css('a'):
                    href = a.css('::attr(href)').get() or ''
                    text = a.css('::text').get() or ''
                    
                    # Reliable parameters for facebook pagination
                    if 'cursor=' in href or 'bac=' in href or 'section_index=' in href or 'unit_cursor=' in href:
                        if href.startswith('/'):
                            next_url = f"https://mbasic.facebook.com{href}"
                        else:
                            next_url = href
                        break
                        
                    # Fallback text check
                    if any(k in text.lower() for k in ["show more", "lihat postingan lainnya", "lihat lainnya", "more posts", "postingan lainnya"]):
                        if href.startswith('/'):
                            next_url = f"https://mbasic.facebook.com{href}"
                        else:
                            next_url = href
                        break

                if not next_url or new_posts_found == 0:
                    break

                current_url = next_url
                page_num += 1
                time.sleep(delay)

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
                writer.writerow(["Post ID", "Timestamp", "Content", "Story Link", "Images"])
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
