import os
import re
import sys
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
        console.print(Panel("[bold green]Manga Web Latest Catcher Cleaning[/bold green]\nExtract unique manga titles and their latest chapters from crawled data.", border_style="green"))

        # Find CSV files in exports folder as suggestions
        export_dir = config.get("settings", {}).get("default_export_dir", "exports")
        csv_files = []
        if os.path.exists(export_dir):
            csv_files = [os.path.join(export_dir, f) for f in os.listdir(export_dir) if f.endswith(".csv")]
        
        # Sort files by modification time descending so the latest scraped file is listed first
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
                "Enter path to your scraped CSV file (e.g. exports/data_scraping_facebook.csv):",
                validate=lambda val: True if os.path.exists(val.strip()) else "File does not exist."
            ).ask()
            if not input_file:
                return
            input_file = input_file.strip()
        else:
            # Map choice back to full path
            idx = choices.index(file_choice)
            input_file = csv_files[idx]

        # Suggest output files based on input filename
        base, ext = os.path.splitext(input_file)
        default_out_all = f"{base}_manga_clean.csv"
        default_out_latest = f"{base}_manga_latest_unique.csv"

        out_all = questionary.text("Output file path for ALL cleaned manga records:", default=default_out_all).ask()
        if not out_all:
            return
        out_all = out_all.strip()

        out_latest = questionary.text("Output file path for UNIQUE manga with LATEST chapter:", default=default_out_latest).ask()
        if not out_latest:
            return
        out_latest = out_latest.strip()

        # Regular expressions
        # Menangkap: "Title: X", "Title - X", "Title X", "TITLE: X", "Title ,: X", dll.
        pattern_title = r"Title\s*[:,;~\-=\s]*\s*(.*?)(?=\n|$)"
        # Mendukung chapter angka bulat maupun desimal (contoh: Chapter 6.1, Chapter 59.5, Chapter 88)
        pattern_chapter = r"Chapter\s*[:,;~\-=\s]*\s*(\d+(?:\.\d+)?)"

        compiled_data = []

        console.print(f"[yellow]Reading {input_file} in chunks...[/yellow]")
        
        try:
            # Load data in chunks to save RAM
            chunks = pd.read_csv(input_file, chunksize=100000)
            
            total_rows_processed = 0
            extracted_count = 0

            for chunk in chunks:
                total_rows_processed += len(chunk)
                
                # Check if 'Content' column exists
                if 'Content' not in chunk.columns:
                    console.print("[bold red]Error: CSV file must contain a 'Content' column.[/bold red]")
                    return
                
                # Filter rows where Content contains 'Title' (case insensitive)
                filtered_chunk = chunk[chunk['Content'].fillna('').str.contains(r'Title', case=False, regex=True)]
                
                for idx, row in filtered_chunk.iterrows():
                    content = str(row['Content'])
                    
                    # Extract Title
                    title_match = re.search(pattern_title, content, re.IGNORECASE)
                    title = title_match.group(1).strip() if title_match else None
                    
                    # Extract Chapter
                    chapter_match = re.search(pattern_chapter, content, re.IGNORECASE)
                    chapter = chapter_match.group(1).strip() if chapter_match else None
                    
                    # Save if title is found
                    if title:
                        extracted_count += 1
                        compiled_data.append({
                            'Post ID': row.get('Post ID', ''),
                            'Timestamp': row.get('Timestamp', ''),
                            'Title': title,
                            'Chapter': chapter
                        })

            if not compiled_data:
                console.print("[yellow]No manga title/chapter found in the content of the selected file.[/yellow]")
                return

            df_clean = pd.DataFrame(compiled_data)
            
            # Export all cleaned records
            df_clean.to_csv(out_all, index=False)
            console.print(f"[green]✓ All cleaned data saved: [bold]{out_all}[/bold] ({len(df_clean)} rows)[/green]")

            # Now, process unique manga with latest chapter
            # We convert chapter to numeric to find the maximum/latest chapter.
            df_clean['Chapter_Num'] = pd.to_numeric(df_clean['Chapter'], errors='coerce')
            
            # Handle Timestamp for sorting as a fallback
            df_clean['Timestamp_DT'] = pd.to_datetime(df_clean['Timestamp'], errors='coerce')

            # Create a normalized Title column for case-insensitive deduplication
            df_clean['Title_Normalized'] = df_clean['Title'].str.strip().str.lower()

            # Sort: Title_Normalized (alphabetical), Chapter_Num (descending, NaNs last), Timestamp_DT (descending)
            df_clean = df_clean.sort_values(
                by=['Title_Normalized', 'Chapter_Num', 'Timestamp_DT'], 
                ascending=[True, False, False]
            )

            # Drop duplicates based on normalized title, keeping the first occurrence (highest chapter / latest timestamp)
            df_latest = df_clean.drop_duplicates(subset=['Title_Normalized'], keep='first')

            # Remove temporary columns before saving
            df_latest = df_latest.drop(columns=['Title_Normalized', 'Chapter_Num', 'Timestamp_DT'], errors='ignore')

            # Export unique latest records
            df_latest.to_csv(out_latest, index=False)
            console.print(f"[green]✓ Unique latest chapter data saved: [bold]{out_latest}[/bold] ({len(df_latest)} rows)[/green]")

            # Print a preview table of the unique latest chapters (top 10)
            table = Table(title="Cleaned Manga Preview (Latest Chapter)", show_header=True, header_style="bold magenta")
            table.add_column("Title", style="cyan", width=45)
            table.add_column("Latest Chapter", justify="center", style="green", width=15)
            table.add_column("Post ID", style="dim", width=15)
            table.add_column("Timestamp", width=20)

            for idx, row in df_latest.head(10).iterrows():
                table.add_row(
                    str(row['Title']),
                    str(row['Chapter']) if pd.notna(row['Chapter']) else "N/A",
                    str(row['Post ID']),
                    str(row['Timestamp'])
                )
            console.print(table)

            if len(df_latest) > 10:
                console.print(f"[dim]... and {len(df_latest) - 10} more unique manga entries.[/dim]")

        except Exception as e:
            console.print(f"[bold red]An error occurred during cleaning: {e}[/bold red]")
