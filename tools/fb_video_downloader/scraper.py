import os
import sys
import json
import csv
import re
import time
import math
import random
import urllib.parse as urlparse
from datetime import datetime

# Reconfigure stdout to UTF-8 to prevent encoding errors on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# Add project root directory to sys.path if run directly
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn,
    DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, MofNCompleteColumn
)
import questionary

from core.base_tool import BaseTool
from core.engine import ScrapingEngine

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse target from URL or username
# ─────────────────────────────────────────────────────────────────────────────

def parse_target(raw: str) -> str:
    raw = raw.strip()
    if "facebook.com" not in raw:
        return raw
    if "profile.php" in raw:
        parsed = urlparse.urlparse(raw)
        qs = urlparse.parse_qs(parsed.query)
        return qs.get("id", [raw])[0]
    clean = raw
    for prefix in ["https://", "http://", "www.", "mbasic.", "m.", "web."]:
        if prefix in clean:
            clean = clean.split(prefix, 1)[-1]
    parts = clean.split("facebook.com/", 1)[-1].strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "people":
        return parts[2]
    target = parts[0].split("?")[0] if parts else raw
    return target


# ─────────────────────────────────────────────────────────────────────────────
# Helper: recursively search dict/list for video CDN URLs and metadata
# ─────────────────────────────────────────────────────────────────────────────

def find_in(data, *keys):
    """Recursively search for ANY of the given keys and return the first value found."""
    if isinstance(data, dict):
        for k in keys:
            if k in data and data[k]:
                return data[k]
        for v in data.values():
            result = find_in(v, *keys)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_in(item, *keys)
            if result:
                return result
    return None


def extract_video_entries(blob: dict) -> list[dict]:
    """
    Walk a GraphQL JSON blob and extract video node entries.
    Returns a list of dicts with keys: video_id, hd_url, sd_url, title, timestamp, post_url.
    """
    entries = []
    _collect_video_nodes(blob, entries, seen=set())
    return entries


def _collect_video_nodes(data, results: list, seen: set):
    if isinstance(data, dict):
        typename = data.get("__typename", "")

        # --- Primary: CometVideoRoot / VideoChannel / Video node ---
        is_video_node = (
            typename in ("Video", "UnifiedVideo", "CometVideoRoot",
                         "VideoChannelEpisode", "StoryAttachmentStyle")
            or "playable_url" in data
            or "playable_url_quality_hd" in data
            or "browser_native_hd_url" in data
            or "browser_native_sd_url" in data
        )

        if is_video_node:
            hd_url  = (data.get("playable_url_quality_hd")
                       or data.get("browser_native_hd_url")
                       or data.get("playable_url") or "")
            sd_url  = (data.get("playable_url")
                       or data.get("browser_native_sd_url") or "")

            # Only keep actual CDN video URLs
            if hd_url and "fbcdn.net" not in hd_url:
                hd_url = ""
            if sd_url and "fbcdn.net" not in sd_url:
                sd_url = ""

            best_url = hd_url or sd_url
            if best_url:
                video_id = (data.get("id")
                            or data.get("video_id")
                            or re.search(r'/(\d{10,})', best_url or "")
                            and re.search(r'/(\d{10,})', best_url).group(1) or "")
                if video_id and video_id not in seen:
                    seen.add(video_id)
                    # Title: look for name / title / message text
                    title_obj = (data.get("name")
                                 or data.get("title")
                                 or find_in(data, "message"))
                    title = ""
                    if isinstance(title_obj, dict):
                        title = title_obj.get("text", "")
                    elif isinstance(title_obj, str):
                        title = title_obj

                    # Timestamp
                    ts_raw = data.get("publish_time") or data.get("creation_time") or find_in(data, "creation_time", "publish_time")
                    ts_fmt = ""
                    if ts_raw:
                        try:
                            ts_fmt = datetime.fromtimestamp(int(ts_raw)).strftime('%Y-%m-%d %H:%M:%S')
                        except Exception:
                            ts_fmt = str(ts_raw)

                    # Post URL
                    post_url = (data.get("url")
                                or find_in(data, "url", "permalink_url")
                                or "")
                    if post_url and not post_url.startswith("http"):
                        post_url = f"https://www.facebook.com{post_url}"

                    results.append({
                        "video_id": str(video_id),
                        "hd_url":   hd_url,
                        "sd_url":   sd_url,
                        "title":    title,
                        "timestamp": ts_fmt,
                        "post_url": post_url,
                        "scraped_at": datetime.now().isoformat(),
                    })

        # --- Recurse into all values ---
        for v in data.values():
            _collect_video_nodes(v, results, seen)

    elif isinstance(data, list):
        for item in data:
            _collect_video_nodes(item, results, seen)


# ─────────────────────────────────────────────────────────────────────────────
# Tool class
# ─────────────────────────────────────────────────────────────────────────────

class FacebookVideoDownloader(BaseTool):

    @property
    def id(self) -> str:
        return "fb_video_downloader"

    @property
    def name(self) -> str:
        return "Facebook Video/Reels Downloader"

    @property
    def description(self) -> str:
        return "Download videos & reels from a Facebook profile or page"

    # ── Entry point ──────────────────────────────────────────────────────────
    def run(self, config: dict, cookies: dict) -> None:
        console.print(Panel(
            "[bold magenta]Facebook Video / Reels Downloader[/bold magenta]\n"
            "Downloads videos and reels from a Facebook profile or page.",
            border_style="magenta"
        ))

        if not cookies:
            console.print("[bold red]Error: No active Facebook cookies. Please set cookies first.[/bold red]")
            return

        # ── Input prompts ────────────────────────────────────────────────────
        raw_target = questionary.text(
            "Enter Facebook Username, Profile ID, or Profile URL:",
            validate=lambda v: True if v.strip() else "Target cannot be empty."
        ).ask()
        if not raw_target:
            return
        target = parse_target(raw_target)

        limit_str = questionary.text(
            "Max videos to download (use -1 for all):",
            default="10"
        ).ask()
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 10
        scrape_all   = limit <= 0
        slice_limit  = 999_999 if scrape_all else limit
        display_limit = "All" if scrape_all else str(limit)

        quality = questionary.select(
            "Preferred video quality:",
            choices=["HD (fallback SD)", "SD only"]
        ).ask()
        prefer_hd = quality == "HD (fallback SD)"

        # Pages to scrape
        page_choices = questionary.checkbox(
            "Which sections to scrape?",
            choices=["Videos", "Reels"],
        ).ask()
        if not page_choices:
            console.print("[yellow]No section selected. Aborting.[/yellow]")
            return

        # ── Anti-bot settings ────────────────────────────────────────────────
        settings = config.get("settings", {})
        max_scroll_wait = settings.get("max_scroll_wait_seconds", 180)
        scroll_freq_min = settings.get("scroll_freq_min", 1)
        scroll_freq_max = settings.get("scroll_freq_max", 3)

        console.print(f"[dim]Anti-bot settings loaded from config: "
                      f"max_wait={max_scroll_wait}s, "
                      f"freq=[{scroll_freq_min}-{scroll_freq_max}] scrolls[/dim]")

        # ── Export setup ─────────────────────────────────────────────────────
        export_dir = settings.get("default_export_dir", "exports")
        video_dir  = os.path.join(export_dir, f"videos_{target}")
        os.makedirs(video_dir, exist_ok=True)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_file = os.path.join(export_dir, f"fb_videos_{target}_{timestamp_str}.csv")
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                "Video ID", "Title", "Timestamp", "Quality Downloaded",
                "Post URL", "Local File", "HD URL", "SD URL"
            ])

        # ── State ────────────────────────────────────────────────────────────
        engine = ScrapingEngine(config)
        collected_videos: list[dict] = []   # metadata only (small, no raw bytes)
        written_ids: set[str] = set()
        graphql_data: list[str] = []
        downloaded_count = 0

        # Cookie header for download requests
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        dl_headers = {
            "User-Agent": settings.get("user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"),
            "Cookie":  cookie_header,
            "Referer": "https://www.facebook.com/",
        }

        # ── Inner helpers ────────────────────────────────────────────────────

        def flush_graphql_to_disk():
            """Parse current graphql_data batch, deduplicate, download new videos."""
            nonlocal downloaded_count

            new_blobs: list[dict] = []
            for g_text in graphql_data:
                for line in g_text.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        new_blobs.append(json.loads(line))
                    except Exception:
                        pass
            graphql_data.clear()   # ← free RAM immediately

            new_entries: list[dict] = []
            for blob in new_blobs:
                new_entries.extend(extract_video_entries(blob))

            for entry in new_entries:
                vid = entry["video_id"]
                if vid in written_ids:
                    continue
                if not scrape_all and downloaded_count >= slice_limit:
                    break
                written_ids.add(vid)

                chosen_url = (entry["hd_url"] if prefer_hd else "") or entry["sd_url"] or entry["hd_url"]
                quality_label = "HD" if chosen_url == entry["hd_url"] and entry["hd_url"] else "SD"
                if not chosen_url:
                    console.print(f"[yellow]⚠ Video {vid}: No downloadable URL found, skipping.[/yellow]")
                    continue

                # Sanitise filename
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', entry["title"])[:60] if entry["title"] else vid
                filename = f"{vid}_{safe_title}.mp4"
                local_path = os.path.join(video_dir, filename)

                # Download with per-file progress bar
                success = _download_file(chosen_url, local_path, dl_headers, vid, quality_label)

                entry["local_file"]         = os.path.abspath(local_path) if success else "FAILED"
                entry["quality_downloaded"] = quality_label if success else "FAILED"
                collected_videos.append(entry)

                # Append row to CSV immediately
                with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                    csv.writer(f).writerow([
                        entry["video_id"],
                        entry["title"],
                        entry["timestamp"],
                        entry["quality_downloaded"],
                        entry["post_url"],
                        entry["local_file"],
                        entry["hd_url"],
                        entry["sd_url"],
                    ])

                if success:
                    downloaded_count += 1
                    console.print(
                        f"[green]✓ Downloaded [{quality_label}] "
                        f"({downloaded_count}/{display_limit}): {filename}[/green]"
                    )

        def _download_file(url: str, path: str, headers: dict,
                           vid: str, quality: str) -> bool:
            """Stream-download a video file with a rich progress bar."""
            try:
                resp = requests.get(url, headers=headers, stream=True, timeout=60)
                if resp.status_code != 200:
                    console.print(f"[red]✗ HTTP {resp.status_code} downloading video {vid}[/red]")
                    return False

                total = int(resp.headers.get("Content-Length", 0))
                console.print(f"[cyan]⬇ Downloading video {vid} [{quality}] "
                              f"({total // 1024 / 1024:.1f} MB)...[/cyan]")

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                    transient=True,
                ) as prog:
                    dl_task = prog.add_task(
                        f"[magenta]{os.path.basename(path)}[/magenta]",
                        total=total if total else None
                    )
                    with open(path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                                prog.update(dl_task, advance=len(chunk))

                return True
            except Exception as e:
                console.print(f"[red]✗ Error downloading video {vid}: {e}[/red]")
                return False

        def make_scroll_action(section_label: str):
            """
            Returns a Playwright page_action callback for one section (videos / reels).
            """
            def scroll_action(page):
                # Intercept network responses — capture GraphQL payloads
                def on_response(response):
                    url = response.url
                    ct  = response.headers.get("content-type", "")
                    # GraphQL / JSON payloads
                    if ("graphql" in url or "api/graphql" in url) and "json" in ct:
                        try:
                            text = response.text()
                            if any(k in text for k in [
                                "playable_url", "browser_native_hd_url",
                                "VideoChannel", "CometVideoRoot", "UnifiedVideo"
                            ]):
                                graphql_data.append(text)
                                sz = len(text)
                                console.print(
                                    f"[bold green]✓[/bold green] "
                                    f"[cyan]Captured video GraphQL packet "
                                    f"({sz:,} bytes)[/cyan]"
                                )
                        except Exception:
                            pass

                page.on("response", on_response)

                # Wait for first video elements to render
                console.print(f"[cyan]Waiting for {section_label} feed to render...[/cyan]")
                try:
                    page.wait_for_selector(
                        'div[data-pagelet*="Video"], div[role="main"] a[href*="/videos/"],'
                        'div[role="main"] a[href*="/reel/"]',
                        timeout=15000
                    )
                except Exception:
                    page.wait_for_timeout(4000)

                last_height = page.evaluate("document.body.scrollHeight")
                scroll_count = 0
                target_scrolls = 9999 if scrape_all else max(2, math.ceil(slice_limit / 4))

                console.print(
                    f"[green]Feed loaded. Starting scroll "
                    f"(target: {'Unlimited' if scrape_all else target_scrolls} scrolls)[/green]\n"
                )

                scrolls_to_wait = random.randint(scroll_freq_min, scroll_freq_max)
                next_wait_at    = scroll_count + scrolls_to_wait
                console.print(
                    f"[dim]Anti-bot plan: first pause after {scrolls_to_wait} scrolls "
                    f"(Scroll #{next_wait_at})[/dim]"
                )

                while scroll_count < target_scrolls:
                    scroll_count += 1
                    console.print(
                        f"[bold blue]>>> [{section_label}] Scroll "
                        f"#{scroll_count} / {'∞' if scrape_all else target_scrolls}[/bold blue] "
                        f"[dim](height {last_height}px)[/dim]"
                    )

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    console.print(f"[cyan]  ↓ Scrolled — waiting for network...[/cyan]")
                    page.wait_for_timeout(4000)

                    new_height = page.evaluate("document.body.scrollHeight")

                    # Nudge if height unchanged
                    if new_height == last_height:
                        console.print(
                            f"[yellow]  ⚠ Height unchanged ({new_height}px). "
                            f"Nudging scroll...[/yellow]"
                        )
                        page.evaluate("window.scrollBy(0, -500)")
                        page.wait_for_timeout(1500)
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(4000)
                        new_height = page.evaluate("document.body.scrollHeight")

                    console.print(f"[dim]  Height: {last_height}px → {new_height}px[/dim]")

                    if new_height == last_height:
                        console.print(
                            "[bold green]  ✓ Reached end of feed. "
                            "Stopping scroll.[/bold green]"
                        )
                        break

                    last_height = new_height

                    # Flush captured packets to disk + download videos
                    if graphql_data:
                        console.print(
                            f"[magenta]  ℹ Flushing {len(graphql_data)} packet(s) "
                            f"→ parsing & downloading...[/magenta]"
                        )
                        flush_graphql_to_disk()
                        console.print(
                            f"[magenta]  ✓ Total downloaded so far: "
                            f"{downloaded_count}/{display_limit}[/magenta]\n"
                        )

                    # Check limit
                    if not scrape_all and downloaded_count >= slice_limit:
                        console.print(
                            f"[bold green]  ✓ Reached download limit "
                            f"({slice_limit}). Stopping.[/bold green]"
                        )
                        break

                    # Anti-bot pause
                    if scroll_count >= next_wait_at:
                        scrolls_to_wait = random.randint(scroll_freq_min, scroll_freq_max)
                        next_wait_at    = scroll_count + scrolls_to_wait

                        if max_scroll_wait > 0:
                            delay = random.randint(0, max_scroll_wait)
                            if delay > 0:
                                console.print(
                                    f"[yellow]⏱ Anti-bot pause: {delay}s "
                                    f"(randomized 0-{max_scroll_wait}s)[/yellow]"
                                )
                                console.print(
                                    f"[dim]Next pause in {scrolls_to_wait} scrolls "
                                    f"(Scroll #{next_wait_at})[/dim]"
                                )
                                for elapsed in range(1, delay + 1):
                                    remaining = delay - elapsed
                                    console.print(
                                        f"[yellow]   ⏳ {elapsed:>3}s elapsed | "
                                        f"{remaining:>3}s remaining[/yellow]",
                                        end="\r"
                                    )
                                    time.sleep(1)
                                console.print()
                                console.print("[green]  ✓ Pause finished. Resuming...[/green]\n")
                            else:
                                console.print("[dim]  ⏱ 0s delay selected. Resuming.[/dim]")
                        else:
                            console.print("[dim]  ⏱ Anti-bot pauses disabled.[/dim]")
                    else:
                        left = next_wait_at - scroll_count
                        console.print(
                            f"[dim]  No pause. Next pause in {left} scrolls "
                            f"(Scroll #{next_wait_at})[/dim]\n"
                        )

            return scroll_action

        # ── Scrape each selected section ─────────────────────────────────────

        section_urls = []
        if "Videos" in page_choices:
            if target.isdigit():
                section_urls.append(
                    (f"https://www.facebook.com/profile.php?id={target}&sk=videos", "Videos")
                )
            else:
                section_urls.append(
                    (f"https://www.facebook.com/{target}/videos", "Videos")
                )
        if "Reels" in page_choices:
            if target.isdigit():
                section_urls.append(
                    (f"https://www.facebook.com/profile.php?id={target}&sk=reels", "Reels")
                )
            else:
                section_urls.append(
                    (f"https://www.facebook.com/{target}/reels", "Reels")
                )

        for section_url, section_label in section_urls:
            if not scrape_all and downloaded_count >= slice_limit:
                console.print(f"[green]Download limit reached — skipping {section_label}.[/green]")
                break

            console.print(f"\n[bold yellow]📹 Starting [{section_label}] section: {section_url}[/bold yellow]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as prog:
                prog.add_task(f"[cyan]Scrolling {section_label} feed...", total=None)
                try:
                    selector = engine.fetch_page(
                        section_url,
                        page_action=make_scroll_action(section_label)
                    )
                except Exception as e:
                    console.print(f"[bold red]Error fetching {section_label}: {e}[/bold red]")
                    continue

            if "login" in selector.url or "login.php" in selector.url:
                console.print("[bold red]Session expired or blocked. Redirected to login.[/bold red]")
                break

            # Final flush of any remaining packets
            if graphql_data:
                console.print(f"[cyan]Final flush for {section_label}...[/cyan]")
                flush_graphql_to_disk()

            console.print(
                f"[green]✓ [{section_label}] done — "
                f"{downloaded_count} video(s) downloaded so far.[/green]"
            )

        # ── Summary ──────────────────────────────────────────────────────────
        console.print(f"\n[bold green]═══════ Download Complete ═══════[/bold green]")
        console.print(f"[green]Total videos downloaded: [bold]{downloaded_count}[/bold][/green]")
        console.print(f"[green]Videos saved to: [bold underline]{os.path.abspath(video_dir)}[/bold underline][/green]")
        console.print(f"[green]CSV metadata: [bold underline]{os.path.abspath(csv_file)}[/bold underline][/green]")

        if collected_videos:
            table = Table(
                title="Downloaded Videos Preview",
                show_header=True,
                header_style="bold magenta"
            )
            table.add_column("Video ID",  style="dim",    width=18)
            table.add_column("Quality",                   width=5)
            table.add_column("Timestamp",                 width=19)
            table.add_column("Title",                     width=35)
            table.add_column("File",      style="green",  width=35)

            for v in collected_videos[:10]:
                title_preview = (v["title"][:32] + "…") if len(v["title"]) > 35 else v["title"]
                file_preview  = os.path.basename(v.get("local_file", ""))
                table.add_row(
                    v["video_id"],
                    v.get("quality_downloaded", "?"),
                    v["timestamp"] or "Unknown",
                    title_preview,
                    file_preview,
                )
            console.print(table)
        else:
            console.print("\n[bold yellow]No videos were downloaded. Possible reasons:[/bold yellow]")
            console.print("1. The profile is private or has no public videos/reels.")
            console.print("2. Facebook blocked the session — try refreshing cookies.")
            console.print("3. No video GraphQL packets were intercepted (profile may use a different layout).")


if __name__ == "__main__":
    console.print("\n[bold yellow]⚠ This is a plugin module. Run via:[/bold yellow]")
    console.print("  [bold green]python main.py[/bold green]\n")
