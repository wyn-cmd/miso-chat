import os
import sys
import json
import re

from dotenv_loader import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from google import genai
from rich.console import Console
from rich.rule import Rule
from rich.panel import Panel


from rich.markup import escape
from rich.text import Text

def highlight_quotes(t):
    # Escape any rich markup brackets in the original text first so they don't crash Rich
    escaped = escape(t)
    def repl(match):
        return f"[green]{match.group(0)}[/green]"
    return re.sub(r'"([^"]*)"', repl, escaped)

console = Console()

# GEMINI_API_KEY is read from the environment or .env (see load_dotenv above)

from rp_core import Engine, get_request_bar, timestamp_now
from charname import display_for, resolve_folder

# ---------------------------------------------------------------------------
# Terminal front end. The chat logic lives in rp_core.Engine, which prints nothing.
# ---------------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] == "--group":
    if len(sys.argv) < 4:
        print("Usage: python roleplay.py --group <GroupName> <Char1> <Char2> ...")
        sys.exit(1)
    g_name = resolve_folder(sys.argv[2])
    g_members = [resolve_folder(m) for m in sys.argv[3:]]
    os.makedirs(g_name, exist_ok=True)
    cfg_path = os.path.join(g_name, "group_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"members": g_members}, f, indent=2)
    print(f"Group '{display_for(g_name)}' created successfully with members: {[display_for(m) for m in g_members]}")
    sys.exit(0)

if len(sys.argv) < 2:
    console.print("[bold red]Error: Please specify a character to chat with![/bold red]")
    console.print("Usage: [cyan]python roleplay.py <CharacterFolder> [UserPersonaFolder][/cyan]")
    subdirs = [d for d in os.listdir(".") if os.path.isdir(d) and d not in [".git", "__pycache__", ".venv"]]
    if subdirs:
        console.print(f"Available folders: [green]{', '.join(subdirs)}[/green]")
    sys.exit(1)

ai_profile = resolve_folder(sys.argv[1])
user_profile = resolve_folder(sys.argv[2]) if len(sys.argv) > 2 else None

if not os.environ.get("GEMINI_API_KEY"):
    console.print("[bold red]GEMINI_API_KEY is not set. Add it to .env (see .env.example) and run again.[/bold red]")
    sys.exit(1)
client = genai.Client()
eng = Engine(client, ai_profile, user_profile, log=console.print)
eng.discover_models()

created_now = not os.path.exists(ai_profile)
if created_now:
    eng.create_profile_folder()
eng.load_profile()
if created_now:
    summary_text = eng.generate_summary()
    console.print(Panel(Text.from_markup(summary_text), title=f"[bold {eng.character_color}]{escape(eng.character_name)}[/bold {eng.character_color}]", title_align="left", border_style=eng.character_color, padding=(1, 2)))
    console.print("")

if len(eng.full_history) >= 2:
    console.print("\n[dim]--- Last 2 messages in memory ---[/dim]")
    for entry in eng.full_history[-2:]:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role", "user")
        text = eng.restore(entry.get("text", str(entry)))
        name = eng.user_display_name if role == "user" else eng.character_name
        color = eng.user_color if role == "user" else eng.character_color
        console.print(f"[bold {color}][{escape(name)}]:[/bold {color}] {text}")
    console.print("[dim]------------------------------------[/dim]\n")

console.print(f"\n[bold magenta]✨ Miso Chat Active: [{escape(eng.user_display_name)}] chatting with [{escape(eng.character_name)}] (Discovered Models: {len(eng.fallback_models)})! Type 'exit' to quit. ✨[/bold magenta]\n")

VERBOSE_MODE = False

while True:
    try:
        console.print(Rule(style="dim magenta"))
        console.print(f"[bold {eng.user_color}][{escape(eng.user_display_name)}][/bold {eng.user_color}] ", end="")
        user_input = input()
        cmd = user_input.strip().lower()

        if cmd in ["exit", "quit", "/exit", "/quit", "/quit-clear"]:
            if cmd == "/quit-clear":
                console.clear()
            console.print(Rule(style="dim magenta"))
            console.print("\n[bold green]Saving final history and updating long-term memory.md... Byeeeee! ( >w<)✨[/bold green]")
            if len(eng.full_history) > 0:
                eng.update_memory_bank()
            break

        if cmd == "/model" or cmd.startswith("/model "):
            arg = user_input.strip()[6:].strip()
            console.print(Rule(style="dim cyan"))
            if not arg:
                console.print(f"[bold cyan]Current model: {eng.model_name} | Pinned: {eng.pinned_model or 'none (auto fallback chain)'}[/bold cyan]")
                console.print(f"[dim]Known: {', '.join(eng.fallback_models[:12])}\nUsage: /model <name> | /model auto[/dim]")
            elif arg.lower() in ("auto", "default", "reset", "off"):
                eng.clear_pin()
                console.print("[bold green]Model pin cleared; using the default fallback chain.[/bold green]")
            else:
                chosen = eng.resolve_model_name(arg)
                if isinstance(chosen, list):
                    console.print(f"[yellow]'{arg}' is ambiguous: {', '.join(chosen)}[/yellow]")
                elif chosen is None:
                    console.print("[yellow]No model name given.[/yellow]")
                else:
                    eng.pin_model(chosen)
                    note = "" if chosen in eng.fallback_models else " (not in discovered list; will fall back if it fails)"
                    console.print(f"[bold green]Model pinned for this session: {chosen}{note}. If it fails, the normal fallback chain takes over.[/bold green]")
            console.print(Rule(style="dim cyan"))
            continue

        if cmd == "/save":
            console.print(Rule(style="dim cyan"))
            console.print("[bold cyan]💾 Manually triggering memory.md memory bank update... (OwO)[/bold cyan]")
            if len(eng.full_history) > 0:
                eng.update_memory_bank()
            else:
                console.print("[yellow]No history to save yet![/yellow]")
            continue

        if cmd == "/history":
            console.print(Rule(style="dim cyan"))
            console.print(f"[bold cyan]📜 Full Chat History ({len(eng.full_history)} messages):[/bold cyan]")
            for entry in eng.full_history:
                text = eng.restore(entry["text"])
                if entry["role"] == "user":
                    console.print(f"[bold {eng.user_color}][{escape(eng.user_display_name)}]:[/bold {eng.user_color}] {text}")
                else:
                    console.print(f"[bold {eng.character_color}][{escape(eng.character_name)}]:[/bold {eng.character_color}] {text}")
            console.print(Rule(style="dim cyan"))
            continue

        if cmd == "/back":
            console.print(Rule(style="dim cyan"))
            if eng.undo():
                console.print(f"[bold yellow]Undid last exchange (removed last user & model message). Total history: {len(eng.full_history)} messages. (owo)[/bold yellow]")
            else:
                console.print("[yellow]Not enough history to go back![/yellow]")
            console.print(Rule(style="dim cyan"))
            continue

        if cmd == "/verbose":
            VERBOSE_MODE = not VERBOSE_MODE
            console.print(f"[bold cyan]VERBOSE MODE: {'ON' if VERBOSE_MODE else 'OFF'} (OwO)[/bold cyan]")
            continue

        processed_input = eng.prepare_input(user_input)

        if VERBOSE_MODE:
            console.print(Rule(style="dim yellow"))
            console.print("[yellow]--- Verbose Diagnostics: Prompts ---[/yellow]")
            console.print(f"[dim]Prompt BEFORE substitutions:[/dim]\n{user_input}")
            console.print(f"[dim]Prompt AFTER substitutions:[/dim]\n{processed_input}")
            console.print(Rule(style="dim yellow"))

        result = eng.send(processed_input)
        if not result.ok:
            console.print(f"[bold red]All available models failed! Last error: {result.error}[/bold red]")
            continue

        if result.selector_note:
            console.print(f"[dim]{result.selector_note}[/dim]")

        for reply in result.replies:
            if eng.is_group:
                console.print(f"[dim]Model: {reply.model} | Requests: {reply.request_bar}[/dim]")
            console.print(Panel(Text.from_markup(highlight_quotes(reply.display)), title=f"[bold {reply.color}]{escape(reply.speaker)}[/bold {reply.color}]", title_align="left", border_style=reply.color, padding=(0, 1)))

        if VERBOSE_MODE:
            console.print(Rule(style="dim yellow"))
            console.print("[yellow]--- Verbose Diagnostics: Response Statistics ---[/yellow]")
            console.print(f"[dim]Timestamp:[/dim] {timestamp_now()}")
            console.print(f"[dim]Model Used:[/dim] {eng.model_name}")
            console.print(f"[dim]Generation Time:[/dim] {result.elapsed:.2f}s")
            console.print(Rule(style="dim yellow"))

        console.print(Rule(style="dim cyan"))
        console.print(f"[dim]Model: {eng.model_name} | {timestamp_now()} | Requests: {get_request_bar()} | Time: {result.elapsed:.2f}s[/dim]")

        if result.memory_due:
            eng.update_memory_bank()

    except KeyboardInterrupt:
        console.print(Rule(style="dim yellow"))
        console.print("\n\n[yellow]Session interrupted. Saving final memory and updating memory.md... Take care! (^V^)[/yellow]")
        if len(eng.full_history) > 0:
            try:
                eng.update_memory_bank()
            except:
                pass
        break
