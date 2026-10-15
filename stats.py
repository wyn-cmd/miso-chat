import os
import json
from rich.console import Console
from rich.table import Table
import datetime

console = Console()

CHARACTER_DIR = "."
TOKEN_TRACKER_FILE = "token_tracker.json"

def estimate_tokens(text):
    return int(len(text) / 4)

def get_stats():
    # 1. Individual Character Statistics Table
    char_folders = [d for d in os.listdir(CHARACTER_DIR) 
                    if os.path.isdir(d) and os.path.exists(os.path.join(d, "character.md")) and not os.path.exists(os.path.join(d, "group_config.json"))]
    
    table = Table(title="Character & Token Statistics")
    table.add_column("Character", style="cyan")
    table.add_column("Msgs", style="green")
    table.add_column("Hist (Est. Tokens)", style="magenta")
    table.add_column("MemBank (Est. Tokens)", style="blue")
    table.add_column("Last Mem Update", style="yellow")
    
    for char in char_folders:
        mem_file = os.path.join(char, "memory.json")
        mem_bank_file = os.path.join(char, "memory.md")
        
        history_len = 0
        hist_tokens = 0
        if os.path.exists(mem_file):
            try:
                with open(mem_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    history_len = len(history)
                    full_text = "".join([entry.get("text", "") for entry in history])
                    hist_tokens = estimate_tokens(full_text)
            except:
                pass
        
        mem_tokens = 0
        if os.path.exists(mem_bank_file):
            with open(mem_bank_file, "r", encoding="utf-8") as f:
                mem_tokens = estimate_tokens(f.read())
            
        last_mod = "N/A"
        if os.path.exists(mem_bank_file):
            last_mod = datetime.datetime.fromtimestamp(os.path.getmtime(mem_bank_file)).strftime('%d/%m/%y %H:%M')
            
        table.add_row(char, str(history_len), f"{hist_tokens:,}", f"{mem_tokens:,}", last_mod)
        
    console.print(table)
    
    # 2. Group Chat Statistics Table
    group_folders = [d for d in os.listdir(CHARACTER_DIR) 
                     if os.path.isdir(d) and os.path.exists(os.path.join(d, "group_config.json"))]
    
    if group_folders:
        console.print("\n")
        g_table = Table(title="Group Chat Statistics")
        g_table.add_column("Group Name", style="cyan")
        g_table.add_column("Members", style="green")
        g_table.add_column("Total Msgs", style="magenta")
        g_table.add_column("Hist Tokens", style="blue")
        g_table.add_column("MemBank (Est. Tokens)", style="magenta")
        g_table.add_column("Last Mem Update", style="yellow")
        g_table.add_column("Per-Member Breakdown", style="cyan")
        
        for g in group_folders:
            cfg_file = os.path.join(g, "group_config.json")
            mem_file = os.path.join(g, "memory.json")
            mem_bank_file = os.path.join(g, "memory.md")
            
            members = []
            if os.path.exists(cfg_file):
                try:
                    with open(cfg_file, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        members = cfg.get("members", [])
                except:
                    pass
            
            history_len = 0
            group_tokens = 0
            
            # Pre-populate counts and tokens for configured members
            speaker_counts = {m: 0 for m in members}
            speaker_tokens = {m: 0 for m in members}
            
            if os.path.exists(mem_file):
                try:
                    with open(mem_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                        history_len = len(history)
                        for entry in history:
                            speaker = entry.get("speaker", entry.get("role", "unknown"))
                            text = entry.get("text", "")
                            toks = estimate_tokens(text)
                            group_tokens += toks
                            
                            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
                            speaker_tokens[speaker] = speaker_tokens.get(speaker, 0) + toks
                except:
                    pass

            group_mem_tokens = 0
            if os.path.exists(mem_bank_file):
                with open(mem_bank_file, "r", encoding="utf-8") as f:
                    group_mem_tokens = estimate_tokens(f.read())

            last_mod = "N/A"
            if os.path.exists(mem_bank_file):
                last_mod = datetime.datetime.fromtimestamp(os.path.getmtime(mem_bank_file)).strftime('%d/%m/%y %H:%M')
            
            breakdown_str = []
            for sp, cnt in speaker_counts.items():
                toks = speaker_tokens.get(sp, 0)
                breakdown_str.append(f"• {sp}: {cnt} msgs ({toks:,} tokens)")
                
            breakdown_display = "\n".join(breakdown_str) if breakdown_str else "No messages recorded"
            
            g_table.add_row(
                g, 
                ", ".join(members), 
                str(history_len), 
                f"{group_tokens:,}", 
                f"{group_mem_tokens:,}",
                last_mod,
                breakdown_display
            )
            g_table.add_section()
            
        console.print(g_table)

    # 3. Daily Request Tracker
    REQUEST_TRACKER_FILE = "request_tracker.json"
    if os.path.exists(REQUEST_TRACKER_FILE):
        with open(REQUEST_TRACKER_FILE, "r", encoding="utf-8") as f:
            t = json.load(f)
            console.print(f"\n[bold magenta]Daily API Requests ({t.get('date', 'N/A')}):[/bold magenta]")
            count = t.get('requests_made', 0)
            limit = 14400
            pct = min(100.0, (count / limit) * 100.0)
            bar_width = 30
            filled = round(bar_width * (pct / 100.0))
            bar_str = "█" * filled + "░" * (bar_width - filled)
            console.print(f"[{bar_str}] {pct:.1f}% ({count}/{limit})")
    else:
        console.print("[yellow]No request tracking data found.[/yellow]")

if __name__ == "__main__":
    get_stats()
