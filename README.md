# Modular Web Scraper CLI using Scrapling

A beautiful, modular CLI scraper built in Python. This tool uses **Scrapling** for web scraping, **questionary** for interactive terminal menus, and **rich** for premium terminal rendering.

It is designed with modularity in mind, allowing you to drop new scraping tools as addons inside the `tools/` directory and have them auto-discovered.

## Features

- 🌟 **Premium CLI Interface**: Dynamic menus, spinner animations, and structured tables.
- 🍪 **Cookie Manager**: Paste, parse, save, and test Facebook authentication cookies via `mbasic.facebook.com`.
- 🔌 **Dynamic Addon Loader**: Drop new scraping scripts in the `tools/` folder and run them immediately from the CLI.
- 📦 **Facebook Post Scraper Addon**: Custom tool to scrape posts, IDs, timestamps, post URLs, and media/images from public profiles/pages.
- 📂 **Auto Export**: Saves data to both `JSON` and `CSV` format in the `exports/` folder.

## Setup Instructions

### Prerequisites

- Python 3.10 or higher installed.

### Installation

1. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

2. Install the dependencies:
   ```bash
   pip install scrapling rich questionary
   ```

3. Run the CLI tool:
   ```bash
   python main.py
   ```

## Adding Custom Scraping Tools (Addons)

To add a new scraping tool:

1. Create a new folder under `tools/` (e.g. `tools/my_new_scraper/`).
2. Create `tools/my_new_scraper/__init__.py` (can be empty).
3. Create `tools/my_new_scraper/scraper.py` and implement the `BaseTool` class:

```python
from core.base_tool import BaseTool

class MyNewScraper(BaseTool):
    @property
    def id(self) -> str:
        return "my_new_scraper"

    @property
    def name(self) -> str:
        return "My New Scraper Tool"

    @property
    def description(self) -> str:
        return "Does something awesome with another website"

    def run(self, config: dict, cookies: dict) -> None:
        print("Scraper is running!")
```

The CLI tool will automatically scan and load your new class on the next startup.

## Cookie Configuration

To scrape Facebook pages that require authentication:
1. Log in to Facebook in your web browser.
2. Open Developer Tools (`F12`), go to the **Network** tab, and reload the page or navigate to `https://mbasic.facebook.com`.
3. Select any request to `mbasic.facebook.com` and find the `Cookie` header in the **Request Headers** section.
4. Copy the entire cookie string.
5. In the CLI application, navigate to **Manage Authentication (Facebook Cookies)** -> **Set new session cookies (Raw string)** and paste the value.
6. Test connection to verify successful authentication.
