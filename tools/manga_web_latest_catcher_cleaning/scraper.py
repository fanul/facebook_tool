import os
import re
import sys
import difflib
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import questionary

# Reconfigure stdout to UTF-8 to prevent encoding errors on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from core.base_tool import BaseTool

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """
    Aggressively normalize a manga title for comparison:
    - Lowercase
    - Collapse repeated punctuation / dashes / spaces
    - Strip leading/trailing noise characters
    """
    t = title.lower().strip()
    # Replace typographic variants of dash/hyphen with a plain dash
    t = re.sub(r'[\u2013\u2014\u2015\u2212\uff0d]', '-', t)
    # Remove repeated dashes/spaces (e.g. "title - -" → "title -")
    t = re.sub(r'[-\s]{2,}', ' ', t)
    # Strip leading/trailing punctuation and spaces
    t = t.strip(' -–—~.,;:!?')
    # Collapse internal whitespace
    t = re.sub(r'\s+', ' ', t)
    return t


def fuzzy_group(titles: list[str], threshold: float = 0.82) -> dict[str, str]:
    """
    Group similar titles using difflib.SequenceMatcher.
    Returns a mapping {original_title -> canonical_title}.
    The canonical title is the shortest normalized form in the group
    (usually the cleanest, least-noisy version).

    threshold: similarity ratio 0..1. Higher = stricter (fewer merges).
               0.82 is a good default — catches "Title -" vs "Title" but
               won't merge genuinely different manga.
    """
    # Build list of (normalized, original) pairs
    pairs = [(normalize_title(t), t) for t in titles]

    # Union-Find for grouping
    parent = {t: t for t, _ in pairs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            # Keep the lexicographically shorter normalized form as root
            if len(ra) <= len(rb):
                parent[rb] = ra
            else:
                parent[ra] = rb

    norm_list = [n for n, _ in pairs]
    for i in range(len(norm_list)):
        for j in range(i + 1, len(norm_list)):
            ratio = difflib.SequenceMatcher(
                None, norm_list[i], norm_list[j], autojunk=False
            ).ratio()
            if ratio >= threshold:
                union(norm_list[i], norm_list[j])

    # Build original→canonical map using the original title of the root
    # (pick the original title that maps to the shortest normalized root)
    root_to_originals: dict[str, list[str]] = {}
    for norm, orig in pairs:
        root = find(norm)
        root_to_originals.setdefault(root, []).append(orig)

    mapping: dict[str, str] = {}
    for root, originals in root_to_originals.items():
        # Canonical = the original with the shortest normalized form
        canonical = min(originals, key=lambda o: len(normalize_title(o)))
        for orig in originals:
            mapping[orig] = canonical

    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# Tool
# ─────────────────────────────────────────────────────────────────────────────

class MangaWebLatestCatcherCleaning(BaseTool):
    @property
    def id(self) -> str:
        return "manga-web-latest-catcher-cleaning"

    @property
    def name(self) -> str:
        return "Manga Web Latest Catcher Cleaning"

    @property
    def description(self) -> str:
        return "Cleans and extracts unique manga titles with their latest chapter from scraped CSV files"

    @property
    def category(self) -> str:
        return "utility"

    def run(self, config: dict, cookies: dict) -> None:
        console.print(Panel(
            "[bold green]Manga Web Latest Catcher Cleaning[/bold green]\n"
            "Extract unique manga titles and their latest chapters from crawled data.\n"
            "[dim]Uses fuzzy matching (difflib) to merge near-duplicate titles.[/dim]",
            border_style="green"
        ))

        # ── File selection ────────────────────────────────────────────────────
        export_dir = config.get("settings", {}).get("default_export_dir", "exports")
        csv_files = []
        if os.path.exists(export_dir):
            csv_files = [
                os.path.join(export_dir, f)
                for f in os.listdir(export_dir)
                if f.endswith(".csv")
            ]
        csv_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)

        choices = [os.path.basename(f) for f in csv_files]
        choices.append("Input path manually...")

        if not csv_files:
            file_choice = "Input path manually..."
        else:
            file_choice = questionary.select(
                "Select a scraped CSV file to clean:",
                choices=choices
            ).ask()

        if file_choice == "Input path manually..." or file_choice is None:
            input_file = questionary.text(
                "Enter path to your scraped CSV file:",
                validate=lambda val: True if os.path.exists(val.strip()) else "File does not exist."
            ).ask()
            if not input_file:
                return
            input_file = input_file.strip()
        else:
            idx = choices.index(file_choice)
            input_file = csv_files[idx]

        # ── Output paths ──────────────────────────────────────────────────────
        base, _ = os.path.splitext(input_file)
        default_out_all    = f"{base}_manga_clean.csv"
        default_out_latest = f"{base}_manga_latest_unique.csv"

        out_all = questionary.text(
            "Output path for ALL cleaned records:", default=default_out_all
        ).ask()
        if not out_all:
            return
        out_all = out_all.strip()

        out_latest = questionary.text(
            "Output path for UNIQUE manga with LATEST chapter:", default=default_out_latest
        ).ask()
        if not out_latest:
            return
        out_latest = out_latest.strip()

        # ── Fuzzy threshold ───────────────────────────────────────────────────
        threshold_str = questionary.text(
            "Fuzzy similarity threshold for merging near-duplicate titles\n"
            "  (0.0 = merge everything, 1.0 = exact match only, recommended: 0.82):",
            default="0.82"
        ).ask()
        try:
            fuzzy_threshold = float(threshold_str)
            fuzzy_threshold = max(0.0, min(1.0, fuzzy_threshold))
        except (ValueError, TypeError):
            fuzzy_threshold = 0.82
        console.print(f"[dim]Fuzzy threshold set to: {fuzzy_threshold}[/dim]")

        # ── Regex patterns ────────────────────────────────────────────────────
        pattern_title   = r"Title\s*[:,;~\-=\s]*\s*(.*?)(?=\n|$)"
        pattern_chapter = r"Chapter\s*[:,;~\-=\s]*\s*(\d+(?:\.\d+)?)"

        compiled_data = []
        console.print(f"[yellow]Reading {input_file} in chunks...[/yellow]")

        try:
            chunks = pd.read_csv(input_file, chunksize=100_000)
            total_rows = 0
            extracted  = 0

            for chunk in chunks:
                total_rows += len(chunk)
                if 'Content' not in chunk.columns:
                    console.print("[bold red]Error: CSV must contain a 'Content' column.[/bold red]")
                    return

                filtered = chunk[
                    chunk['Content'].fillna('').str.contains(r'Title', case=False, regex=True)
                ]

                for _, row in filtered.iterrows():
                    content = str(row['Content'])
                    title_m   = re.search(pattern_title,   content, re.IGNORECASE)
                    chapter_m = re.search(pattern_chapter, content, re.IGNORECASE)
                    title   = title_m.group(1).strip()   if title_m   else None
                    chapter = chapter_m.group(1).strip() if chapter_m else None

                    if title:
                        extracted += 1
                        compiled_data.append({
                            'Post ID':   row.get('Post ID', ''),
                            'Timestamp': row.get('Timestamp', ''),
                            'Title':     title,
                            'Chapter':   chapter,
                        })

            console.print(
                f"[green]Processed {total_rows:,} rows → "
                f"extracted {extracted:,} manga records.[/green]"
            )

            if not compiled_data:
                console.print("[yellow]No manga title/chapter found in the selected file.[/yellow]")
                return

            df_clean = pd.DataFrame(compiled_data)

            # ── Save ALL cleaned records ──────────────────────────────────────
            df_clean.to_csv(out_all, index=False)
            console.print(
                f"[green]✓ All cleaned records saved: [bold]{out_all}[/bold] "
                f"({len(df_clean):,} rows)[/green]"
            )

            # ── Numeric chapter + timestamp for sorting ───────────────────────
            df_clean['Chapter_Num']  = pd.to_numeric(df_clean['Chapter'], errors='coerce')
            df_clean['Timestamp_DT'] = pd.to_datetime(df_clean['Timestamp'], errors='coerce')

            # ── Pass 1: aggressive normalization dedup ────────────────────────
            df_clean['Title_Norm'] = df_clean['Title'].apply(normalize_title)

            # Sort so highest chapter / latest timestamp wins
            df_clean = df_clean.sort_values(
                by=['Title_Norm', 'Chapter_Num', 'Timestamp_DT'],
                ascending=[True, False, False],
                na_position='last'
            )
            df_pass1 = df_clean.drop_duplicates(subset=['Title_Norm'], keep='first').copy()

            before_fuzzy = len(df_pass1)
            console.print(
                f"[dim]After normalization dedup: {before_fuzzy:,} unique titles "
                f"(was {len(df_clean):,})[/dim]"
            )

            # ── Pass 2: fuzzy matching dedup ──────────────────────────────────
            console.print(
                f"[yellow]Running fuzzy grouping on {before_fuzzy:,} titles "
                f"(threshold={fuzzy_threshold})...[/yellow]"
            )
            unique_titles = df_pass1['Title'].tolist()
            title_map = fuzzy_group(unique_titles, threshold=fuzzy_threshold)

            # Apply mapping: replace title with canonical form
            df_pass1['Title_Canonical'] = df_pass1['Title'].map(title_map)

            # Among titles that map to the same canonical, keep highest chapter
            df_pass1 = df_pass1.sort_values(
                by=['Title_Canonical', 'Chapter_Num', 'Timestamp_DT'],
                ascending=[True, False, False],
                na_position='last'
            )
            df_latest = df_pass1.drop_duplicates(subset=['Title_Canonical'], keep='first').copy()

            # Show merge report for any titles that were fuzzy-merged
            merged_groups = {}
            for orig, canon in title_map.items():
                if orig != canon:
                    merged_groups.setdefault(canon, []).append(orig)

            if merged_groups:
                console.print(f"\n[bold yellow]Fuzzy merge report — {len(merged_groups)} group(s) merged:[/bold yellow]")
                for canon, dupes in merged_groups.items():
                    console.print(f"  [green]✓ '{canon}'[/green] ← absorbed:")
                    for d in dupes:
                        console.print(f"      [dim]'{d}'[/dim]")
            else:
                console.print("[dim]No fuzzy merges occurred — all titles were already distinct.[/dim]")

            after_fuzzy = len(df_latest)
            console.print(
                f"\n[green]Deduplication: {before_fuzzy:,} → {after_fuzzy:,} unique manga titles "
                f"({before_fuzzy - after_fuzzy} merged)[/green]"
            )

            # Use the canonical title in the output
            df_latest['Title'] = df_latest['Title_Canonical']

            # Drop temp columns
            drop_cols = ['Title_Norm', 'Title_Canonical', 'Chapter_Num', 'Timestamp_DT']
            df_latest = df_latest.drop(columns=drop_cols, errors='ignore')

            # ── Save unique latest ────────────────────────────────────────────
            df_latest.to_csv(out_latest, index=False)
            console.print(
                f"[green]✓ Unique latest chapter data saved: [bold]{out_latest}[/bold] "
                f"({len(df_latest):,} rows)[/green]"
            )

            # ── Preview table ─────────────────────────────────────────────────
            table = Table(
                title="Cleaned Manga Preview (Latest Chapter, Fuzzy-Deduped)",
                show_header=True, header_style="bold magenta"
            )
            table.add_column("Title",          style="cyan",  width=45)
            table.add_column("Latest Chapter", justify="center", style="green", width=15)
            table.add_column("Post ID",        style="dim",   width=15)
            table.add_column("Timestamp",                     width=20)

            for _, row in df_latest.head(10).iterrows():
                table.add_row(
                    str(row['Title']),
                    str(row['Chapter']) if pd.notna(row['Chapter']) else "N/A",
                    str(row['Post ID']),
                    str(row['Timestamp']),
                )
            console.print(table)

            if len(df_latest) > 10:
                console.print(f"[dim]... and {len(df_latest) - 10} more unique manga entries.[/dim]")

        except Exception as e:
            console.print(f"[bold red]An error occurred during cleaning: {e}[/bold red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
