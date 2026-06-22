import os
import json
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
import questionary

from core.auth import parse_cookie_string, check_facebook_login
from tools import load_tools

CONFIG_PATH = "config.json"
console = Console()

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "active_cookies": {},
        "settings": {
            "default_export_dir": "exports",
            "request_delay_seconds": 2.0,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
        }
    }

def save_config(config: dict) -> None:
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def show_banner():
    banner_text = (
        "[bold cyan]███████╗ ██████╗    ███████╗ ██████╗██████╗  █████╗ ██████╗ ███████╗██████╗ \n"
        "██╔════╝██╔════╝    ██╔════╝██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗\n"
        "█████╗  ██║         ███████╗██║     ██████╔╝███████║██████╔╝█████╗  ██████╔╝\n"
        "██╔══╝  ██║         ╚════██║██║     ██╔══██╗██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗\n"
        "██║     ╚██████╗    ███████║╚██████╗██║  ██║██║  ██║██║     ███████╗██║  ██║\n"
        "╚═╝      ╚═════╝    ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝[/bold cyan]\n"
        "                [italic yellow]Modular Web Scraper Tool suite using Scrapling[/italic yellow]"
    )
    console.print(Panel(Align.center(banner_text), border_style="cyan"))

def manage_auth(config: dict):
    console.print(Panel("[bold green]Manage Authentication[/bold green]\nConfigure and check your Facebook account session cookies.", border_style="green"))
    
    cookies = config.get("active_cookies", {})
    if cookies:
        c_user = cookies.get("c_user", "Unknown")
        console.print(f"[green]Active session detected. c_user (FB User ID): [bold]{c_user}[/bold][/green]")
    else:
        console.print("[yellow]No active session cookies found. You need to log in to scrape protected content.[/yellow]")

    action = questionary.select(
        "What would you like to do?",
        choices=[
            "Test current session connection",
            "Set new session cookies (Raw string)",
            "Clear current session cookies",
            "Back to main menu"
        ]
    ).ask()

    if action == "Test current session connection":
        if not cookies:
            console.print("[red]No cookies set. Cannot test connection.[/red]")
            return
        console.print("[yellow]Testing connection to Facebook mbasic...[/yellow]")
        is_ok, msg = check_facebook_login(cookies, config.get("settings", {}).get("user_agent"))
        if is_ok:
            console.print(f"[bold green]✔ Success: {msg}[/bold green]")
        else:
            console.print(f"[bold red]❌ Failed: {msg}[/bold red]")
            
    elif action == "Set new session cookies (Raw string)":
        console.print("[blue]Please paste your Facebook raw cookie string from browser devtools:[/blue]")
        console.print("[dim]Tip: In Chrome/Firefox, open devtools (F12) -> Network -> Select any mbasic.facebook.com request -> Copy value of 'Cookie' header.[/dim]")
        raw_cookie = questionary.text("Cookie string:").ask()
        if raw_cookie:
            parsed = parse_cookie_string(raw_cookie)
            if parsed and ("c_user" in parsed or "xs" in parsed):
                console.print(f"[yellow]Parsed {len(parsed)} cookies. Testing connection...[/yellow]")
                is_ok, msg = check_facebook_login(parsed, config.get("settings", {}).get("user_agent"))
                if is_ok:
                    config["active_cookies"] = parsed
                    save_config(config)
                    console.print(f"[bold green]✔ Success: {msg}[/bold green]")
                    console.print("[green]Cookies successfully saved to config.json[/green]")
                else:
                    console.print(f"[bold red]❌ Validation failed: {msg}[/bold red]")
                    confirm_save = questionary.confirm("Do you still want to save these cookies?").ask()
                    if confirm_save:
                        config["active_cookies"] = parsed
                        save_config(config)
                        console.print("[green]Cookies saved anyway.[/green]")
            else:
                console.print("[bold red]Failed to parse valid Facebook cookies. Make sure it contains 'c_user' and 'xs' keys.[/bold red]")

    elif action == "Clear current session cookies":
        if questionary.confirm("Are you sure you want to clear active cookies?").ask():
            config["active_cookies"] = {}
            save_config(config)
            console.print("[green]Session cookies cleared.[/green]")

def manage_settings(config: dict):
    console.print(Panel("[bold yellow]Edit Settings[/bold yellow]\nAdjust speed, default paths, and headers.", border_style="yellow"))
    settings = config.setdefault("settings", {})
    
    default_export_dir = settings.get("default_export_dir", "exports")
    request_delay_seconds = settings.get("request_delay_seconds", 2.0)
    user_agent = settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0")

    new_dir = questionary.text("Default export folder path:", default=str(default_export_dir)).ask()
    new_delay_str = questionary.text("Delay between page requests (seconds):", default=str(request_delay_seconds)).ask()
    new_ua = questionary.text("User-Agent header:", default=str(user_agent)).ask()

    try:
        new_delay = float(new_delay_str)
    except ValueError:
        new_delay = 2.0

    settings["default_export_dir"] = new_dir
    settings["request_delay_seconds"] = new_delay
    settings["user_agent"] = new_ua
    
    save_config(config)
    console.print("[bold green]Settings updated and saved successfully.[/bold green]")

def main():
    config = load_config()
    show_banner()
    
    # Load dynamic tools/plugins
    tools_list = load_tools()
    
    while True:
        # Build menu choices
        choices = []
        if tools_list:
            choices.append("Run Scraping Tool")
        choices.extend([
            "Manage Authentication (Facebook Cookies)",
            "Edit Settings",
            "Exit"
        ])
        
        choice = questionary.select(
            "Select an option from the main menu:",
            choices=choices
        ).ask()
        
        if choice == "Run Scraping Tool":
            tool_choices = [f"{t.name} - {t.description}" for t in tools_list]
            tool_choices.append("Back to main menu")
            
            tool_choice = questionary.select(
                "Select a scraper tool to run:",
                choices=tool_choices
            ).ask()
            
            if tool_choice != "Back to main menu":
                selected_name = tool_choice.split(" - ")[0]
                tool_to_run = next((t for t in tools_list if t.name == selected_name), None)
                
                if tool_to_run:
                    try:
                        tool_to_run.run(config, config.get("active_cookies", {}))
                    except Exception as e:
                        console.print(f"[bold red]An unexpected error occurred while running the tool: {e}[/bold red]")
                    
                    questionary.text("\nPress Enter to return to main menu...").ask()
                    
        elif choice == "Manage Authentication (Facebook Cookies)":
            manage_auth(config)
            
        elif choice == "Edit Settings":
            manage_settings(config)
            
        elif choice == "Exit" or choice is None:
            console.print("[bold blue]Thank you for using Modular Scraper Suite. Goodbye![/bold blue]")
            break

if __name__ == "__main__":
    main()
