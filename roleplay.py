import os
import sys
import json
import shutil
import re
import time
import datetime
import random
import socket
import threading
import uuid
import base64
import hashlib

from dotenv_loader import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

USER_ID_FILE = os.path.join(os.path.dirname(__file__), "user_id.json")
def get_or_create_user_id():
    if os.path.exists(USER_ID_FILE):
        try:
            with open(USER_ID_FILE, "r", encoding="utf-8") as f:
                uid = json.load(f).get("user_id")
                if uid:
                    return uid
        except:
            pass
    default_uid = "shared_character_sync_user"
    try:
        with open(USER_ID_FILE, "w", encoding="utf-8") as f:
            json.dump({"user_id": default_uid}, f, indent=2)
    except:
        pass
    return default_uid

MY_USER_ID = get_or_create_user_id()
P2P_PORT = 54322

def pick_sync_root():
    # The folder holding the character folders: normally the script's own
    # folder. If the app was launched from elsewhere (common under Termux) the
    # character folder argument decides, so the sync never walks an unrelated
    # directory such as a home folder.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cwd = os.path.abspath(os.getcwd())
    if cwd == script_dir or cwd.startswith(script_dir + os.sep):
        return script_dir
    for arg in sys.argv[1:3]:
        if not arg:
            continue
        if os.path.isdir(os.path.join(script_dir, arg)):
            return script_dir
        if os.path.isdir(os.path.join(cwd, arg)):
            return cwd
    return script_dir


BASE_DIR = pick_sync_root()
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
SKIP_FILES = {"user_id.json", "device_id.json", "token_tracker.json",
              "request_tracker.json", "config.json", "peers.json"}
PEERS_FILE = os.path.join(BASE_DIR, "peers.json")
TMP_SUFFIX = ".p2ptmp"
BACKUP_SUFFIX = ".p2pbak"
MAX_PACKET = 256 * 1024 * 1024


def _file_hash(path):
    # Content hash so identical files are never re-transferred just because
    # the two devices disagree about mtimes.
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _data_hash(path):
    # Hash of the parsed JSON, so identical turns with different line endings
    # or escaping still compare equal.
    data = _load_json_file(path)
    if data is None:
        return None
    try:
        blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()
    except Exception:
        return None


def get_local_state():
    state = {}
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for file in files:
            if (file in SKIP_FILES or file.endswith(TMP_SUFFIX)
                    or file.endswith(BACKUP_SUFFIX) or file.endswith(".pyc")
                    or ".bak" in file):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, BASE_DIR)
            try:
                info = {"mtime": os.path.getmtime(full_path),
                        "size": os.path.getsize(full_path),
                        "hash": _file_hash(full_path)}
                if file.endswith(".json"):
                    info["dhash"] = _data_hash(full_path)
                state[rel_path] = info
            except OSError:
                pass
    return state


def build_manifest_payload():
    # manifest keeps the rel_path -> mtime shape older versions of this script
    # expect; size and hash ride alongside in meta, which they ignore.
    state = get_local_state()
    return {"manifest": {rel: v["mtime"] for rel, v in state.items()},
            "meta": {rel: {"size": v["size"], "hash": v["hash"], "dhash": v.get("dhash")}
                     for rel, v in state.items()}}


def peer_state(resp):
    raw = resp.get("manifest") or {}
    meta = resp.get("meta") or {}
    state = {}
    for rel, value in raw.items():
        if isinstance(value, dict):
            state[rel] = value
        else:
            extra = meta.get(rel) or {}
            state[rel] = {"mtime": value, "size": extra.get("size"),
                          "hash": extra.get("hash"), "dhash": extra.get("dhash")}
    return state


def local_ipv4_addresses():
    # [(address, netmask)] for the real interfaces, no extra dependencies.
    found = []
    try:
        import fcntl
        import struct
        for _index, name in socket.if_nameindex():
            if name == "lo":
                continue
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                request = struct.pack("256s", name.encode("utf-8")[:15])
                ip = socket.inet_ntoa(fcntl.ioctl(probe.fileno(), 0x8915, request)[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(probe.fileno(), 0x891b, request)[20:24])
                if not ip.startswith("127."):
                    found.append((ip, mask))
            except Exception:
                pass
            finally:
                probe.close()
    except Exception:
        pass
    return found


def local_ip_set():
    ips = {"127.0.0.1", "0.0.0.0", "localhost"}
    for ip, _mask in local_ipv4_addresses():
        ips.add(ip)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except Exception:
        pass
    return ips


def subnet_broadcast(ip, mask):
    try:
        import struct
        addr = struct.unpack("!I", socket.inet_aton(ip))[0]
        bits = struct.unpack("!I", socket.inet_aton(mask))[0]
        return socket.inet_ntoa(struct.pack("!I", (addr & bits) | (~bits & 0xFFFFFFFF)))
    except Exception:
        return None


def discovery_targets():
    # Broadcast is unreliable: Android/Termux will not always send 255.255.255.255
    # and access points drop it, so the subnet broadcast of every interface is
    # tried too, using the real netmask rather than assuming a /24.
    targets = ["255.255.255.255"]
    for ip, mask in local_ipv4_addresses():
        bcast = subnet_broadcast(ip, mask)
        if bcast and bcast not in targets:
            targets.append(bcast)
    return targets


def parse_peer(text):
    text = (text or "").strip()
    if not text:
        return None
    host, sep, port = text.rpartition(":")
    if sep and host and port.isdigit():
        return (host.strip(), int(port))
    return (text, P2P_PORT)


def remembered_peers():
    # Peers written down after a successful sync, plus anything typed into
    # peers.json by hand for networks where broadcast is blocked.
    peers = set()
    data = _load_json_file(PEERS_FILE)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                parsed = parse_peer(item)
            elif isinstance(item, dict) and item.get("host"):
                parsed = (str(item["host"]), int(item.get("port", P2P_PORT)))
            else:
                parsed = None
            if parsed and parsed[0]:
                peers.add(parsed)
    return {p for p in peers if p[0] not in local_ip_set()}


def remember_peer(peer_ip, peer_port):
    entry = "%s:%d" % (peer_ip, peer_port)
    data = _load_json_file(PEERS_FILE)
    if not isinstance(data, list):
        data = []
    if entry in data or peer_ip in local_ip_set():
        return
    data.append(entry)
    try:
        tmp = PEERS_FILE + TMP_SUFFIX
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PEERS_FILE)
    except Exception:
        pass


# --- framing ------------------------------------------------------------------
# TCP has no message boundaries: one recv() returns whatever has arrived, so
# anything past a single segment was truncated and then silently discarded.
def send_packet(sock, obj):
    sock.sendall(json.dumps(obj).encode("utf-8"))


_PACKET_DECODER = json.JSONDecoder()


def recv_packet(sock):
    # Read one JSON packet, however many TCP segments it arrives in.
    #
    # The old code called recv() once and parsed the result, so any reply larger
    # than a single segment was quietly discarded. Keeping the plain JSON wire
    # format also means a device still running the old code can still talk to us.
    data = bytearray()
    while True:
        if data:
            try:
                packet, _ = _PACKET_DECODER.raw_decode(data.decode("utf-8", "ignore"))
                return packet
            except ValueError:
                pass
        if len(data) > MAX_PACKET:
            raise ValueError("packet exceeded %d bytes" % MAX_PACKET)
        chunk = sock.recv(262144)
        if not chunk:
            raise ConnectionError("peer closed after %d bytes of an incomplete packet" % len(data))
        data += chunk


# --- safe writes + history merge ----------------------------------------------
def is_memory_file(rel_path):
    return os.path.basename(rel_path) == "memory.json"


def _load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _entry_key(item):
    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False)
    except Exception:
        return repr(item)


def merge_memory(local_data, peer_data):
    # Union two chat histories: nothing either device wrote is dropped.
    if not isinstance(local_data, list) or not isinstance(peer_data, list):
        return None
    merged = list(local_data)
    have = {_entry_key(i) for i in local_data}
    for item in peer_data:
        if _entry_key(item) not in have:
            merged.append(item)
    return merged


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + TMP_SUFFIX
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _backup(path):
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + BACKUP_SUFFIX)
        except Exception:
            pass


def write_peer_file(rel_path, data, mtime=None):
    dest = os.path.join(BASE_DIR, rel_path)
    if os.path.exists(dest):
        with open(dest, "rb") as f:
            current = f.read()
        if current == data:
            if mtime:
                os.utime(dest, (mtime, mtime))
            return "identical"
        # the empty memory.json a device creates for a new profile must never
        # wipe content that already exists here
        if len(data) <= 4 and len(current) > 4:
            return "refused empty overwrite"
        _backup(dest)
    _atomic_write(dest, data)
    if mtime:
        os.utime(dest, (mtime, mtime))
    return "written"


def merge_memory_on_disk(rel_path, incoming_bytes, mtime=None):
    dest = os.path.join(BASE_DIR, rel_path)
    local = _load_json_file(dest) if os.path.exists(dest) else []
    try:
        incoming = json.loads(incoming_bytes.decode("utf-8"))
    except Exception:
        incoming = None
    if isinstance(local, list) and isinstance(incoming, list):
        merged = merge_memory(local, incoming)
        payload = json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8")
        if os.path.exists(dest):
            with open(dest, "rb") as f:
                current = f.read()
            if current == payload:
                return "identical"
            try:
                if json.loads(current.decode("utf-8")) == merged:
                    return "same content (kept local formatting)"
            except Exception:
                pass
        _backup(dest)
        _atomic_write(dest, payload)
        if mtime:
            os.utime(dest, (mtime, mtime))
        return "merged to %d turns" % len(merged)
    return write_peer_file(rel_path, incoming_bytes, mtime)


def safe_rel_path(rel_path):
    if not rel_path:
        return None
    candidate = os.path.realpath(os.path.join(BASE_DIR, rel_path))
    if candidate != BASE_DIR and not candidate.startswith(BASE_DIR + os.sep):
        return None
    return candidate


# --- server -------------------------------------------------------------------
def p2p_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("", P2P_PORT))
    except OSError as e:
        print(f"[P2P Sync] Cannot listen on port {P2P_PORT}: {e} "
              f"(another copy of roleplay.py already running?)")
        return
    server.listen(5)
    addresses = ", ".join(ip for ip, _mask in local_ipv4_addresses()) or "no LAN address found"
    print(f"[P2P Sync] Listening on port {P2P_PORT} | this device: {addresses} | "
          f"sync folder: {BASE_DIR} | sync runs only on /sync")
    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_peer_connection, args=(conn,), daemon=True).start()
        except Exception:
            time.sleep(0.5)


def handle_peer_connection(conn):
    try:
        conn.settimeout(30)
        packet = recv_packet(conn)
        if packet.get("user_id") != MY_USER_ID:
            send_packet(conn, {"status": "REJECTED"})
            return

        pkt_type = packet.get("type")

        if pkt_type == "MANIFEST_REQUEST":
            payload = {"status": "OK", "user_id": MY_USER_ID}
            payload.update(build_manifest_payload())
            send_packet(conn, payload)

        elif pkt_type == "FILE_REQUEST":
            full_path = safe_rel_path(packet.get("path"))
            if full_path and os.path.isfile(full_path):
                with open(full_path, "rb") as f:
                    raw = f.read()
                send_packet(conn, {"status": "OK",
                                   "content_b64": base64.b64encode(raw).decode("utf-8"),
                                   "mtime": os.path.getmtime(full_path)})
            else:
                send_packet(conn, {"status": "NOT_FOUND"})

        elif pkt_type == "FILE_PUSH":
            rel_path = packet.get("path")
            full_path = safe_rel_path(rel_path)
            if not full_path:
                send_packet(conn, {"status": "REJECTED", "reason": "path outside sync root"})
                return
            raw = base64.b64decode(packet.get("content_b64") or "")
            if is_memory_file(rel_path):
                result = merge_memory_on_disk(rel_path, raw, packet.get("mtime"))
            else:
                result = write_peer_file(rel_path, raw, packet.get("mtime"))
            send_packet(conn, {"status": "RECEIVED", "result": result})

        else:
            send_packet(conn, {"status": "UNKNOWN_TYPE"})
    except Exception as e:
        try:
            send_packet(conn, {"status": "ERROR", "error": "%s: %s" % (type(e).__name__, e)})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --- client -------------------------------------------------------------------
def pull_file(peer_ip, peer_port, rel_path, peer_mtime=None):
    sock = socket.create_connection((peer_ip, peer_port), 4.0)
    try:
        sock.settimeout(30)
        send_packet(sock, {"type": "FILE_REQUEST", "user_id": MY_USER_ID, "path": rel_path})
        resp = recv_packet(sock)
    finally:
        sock.close()
    if resp.get("status") != "OK":
        print(f"[P2P Sync] Peer has no {rel_path} ({resp.get('status')})")
        return False
    raw = base64.b64decode(resp.get("content_b64") or "")
    if is_memory_file(rel_path):
        result = merge_memory_on_disk(rel_path, raw, None)
    else:
        result = write_peer_file(rel_path, raw, resp.get("mtime") or peer_mtime)
    print(f"[P2P Sync] Pulled {rel_path} ({result}, {len(raw)} bytes)")
    return True


def push_file(peer_ip, peer_port, rel_path, local_mtime):
    full_path = safe_rel_path(rel_path)
    if not full_path or not os.path.isfile(full_path):
        return False
    with open(full_path, "rb") as f:
        raw = f.read()
    sock = socket.create_connection((peer_ip, peer_port), 4.0)
    try:
        sock.settimeout(30)
        send_packet(sock, {"type": "FILE_PUSH", "user_id": MY_USER_ID, "path": rel_path,
                           "content_b64": base64.b64encode(raw).decode("utf-8"),
                           "mtime": local_mtime})
        resp = recv_packet(sock)
    finally:
        sock.close()
    print(f"[P2P Sync] Pushed {rel_path} "
          f"({resp.get('result') or resp.get('status')}, {len(raw)} bytes)")
    return resp.get("status") == "RECEIVED"


def sync_with_peer(peer_ip, peer_port):
    sock = socket.create_connection((peer_ip, peer_port), 4.0)
    try:
        sock.settimeout(30)
        send_packet(sock, {"type": "MANIFEST_REQUEST", "user_id": MY_USER_ID})
        resp = recv_packet(sock)
    finally:
        sock.close()
    if resp.get("status") != "OK":
        print(f"[P2P Sync] Peer {peer_ip}:{peer_port} refused the manifest request")
        return

    peer_manifest = peer_state(resp)
    local_manifest = get_local_state()
    pulled = pushed = failed = 0

    for fpath in sorted(set(peer_manifest) | set(local_manifest)):
        try:
            local = local_manifest.get(fpath) or {}
            peer = peer_manifest.get(fpath) or {}

            if is_memory_file(fpath):
                # Chat history is never decided by "newest mtime wins": both
                # copies are unioned, so a turn written on either device lives.
                if fpath not in peer_manifest:
                    if push_file(peer_ip, peer_port, fpath, local.get("mtime")):
                        pushed += 1
                    continue
                same_turns = (local.get("dhash") is not None
                              and local.get("dhash") == peer.get("dhash"))
                if not same_turns and pull_file(peer_ip, peer_port, fpath, peer.get("mtime")):
                    pulled += 1
                local_size = local.get("size") or 0
                if peer.get("size") is not None and peer.get("size") < local_size:
                    if push_file(peer_ip, peer_port, fpath, local.get("mtime")):
                        pushed += 1
                continue

            if local.get("hash") and local.get("hash") == peer.get("hash"):
                continue  # same bytes, nothing to do whatever the clocks say

            local_mtime = local.get("mtime", 0)
            peer_mtime = peer.get("mtime", 0)
            if local_mtime == 0 or peer_mtime > local_mtime:
                if pull_file(peer_ip, peer_port, fpath, peer_mtime):
                    pulled += 1
            elif local_mtime > peer_mtime:
                if push_file(peer_ip, peer_port, fpath, local_mtime):
                    pushed += 1
            elif local.get("size") != peer.get("size"):
                # same clock time, different content: take the peer's copy
                if pull_file(peer_ip, peer_port, fpath, peer_mtime):
                    pulled += 1
        except Exception as e:
            # one bad file must not stop the rest of the sync
            failed += 1
            print(f"[P2P Sync] Failed on {fpath}: {type(e).__name__}: {e}")

    print(f"[P2P Sync] {peer_ip}:{peer_port} -> pulled {pulled}, pushed {pushed}, failed {failed}")


def run_local_p2p_sync(extra_peers=None):
    peers = set(remembered_peers())
    for item in (extra_peers or []):
        parsed = parse_peer(item)
        if parsed:
            peers.add(parsed)

    try:
        print("[P2P Sync] Scanning local Wi-Fi for peers...")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.4)

        ping = json.dumps({"type": "P2P_DISCOVER", "user_id": MY_USER_ID,
                           "port": P2P_PORT}).encode("utf-8")
        targets = discovery_targets()
        deadline = time.time() + 4.0
        next_ping = 0.0
        while time.time() < deadline:
            if time.time() >= next_ping:
                for target in targets:
                    try:
                        s.sendto(ping, (target, P2P_PORT))
                    except Exception:
                        pass
                next_ping = time.time() + 1.2
                if peers:
                    break
            try:
                data, addr = s.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("user_id") == MY_USER_ID and msg.get("port"):
                    peers.add((addr[0], int(msg["port"])))
            except socket.timeout:
                continue
            except Exception:
                pass
        s.close()
    except Exception as e:
        print(f"[P2P Sync] Peer discovery failed: {type(e).__name__}: {e}")

    my_ips = local_ip_set()
    peers = {p for p in peers if p[0] and not (p[0] in my_ips and p[1] == P2P_PORT)}
    if not peers:
        print("[P2P Sync] No peer answered on this network (searched: %s)."
              % ", ".join(discovery_targets()))
        print("[P2P Sync] If the other device is reachable, add its address to "
              "peers.json, e.g. [\"192.0.2.10\"], or use /sync <address>.")
        return
    print(f"[P2P Sync] Sync partners: {sorted(peers)}")

    for peer_ip, peer_port in sorted(peers):
        try:
            sync_with_peer(peer_ip, peer_port)
            remember_peer(peer_ip, peer_port)
        except Exception as e:
            print(f"[P2P Sync] Peer {peer_ip}:{peer_port} failed: {type(e).__name__}: {e}")


def udp_discovery_responder():
    us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    us.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        us.bind(("", P2P_PORT))
    except OSError as e:
        print(f"[P2P Sync] Cannot answer discovery on port {P2P_PORT}: {e}")
        return
    while True:
        try:
            data, addr = us.recvfrom(1024)
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "P2P_DISCOVER" and msg.get("user_id") == MY_USER_ID:
                reply = json.dumps({"type": "P2P_RESPONSE", "user_id": MY_USER_ID, "port": P2P_PORT})
                us.sendto(reply.encode("utf-8"), addr)
        except Exception:
            pass


if os.path.abspath(os.getcwd()) != BASE_DIR:
    print(f"[P2P Sync] Note: started from {os.path.abspath(os.getcwd())} but syncing "
          f"{BASE_DIR}. Launch roleplay.py from its own folder so the two devices "
          f"keep the same layout.")

threading.Thread(target=p2p_tcp_server, daemon=True).start()
threading.Thread(target=udp_discovery_responder, daemon=True).start()
# Sync is manual: it runs only when you type /sync. Nothing syncs at startup and
# nothing syncs on a timer. These two listeners only answer whoever asks.
from google import genai
from google.genai import types
from rich.console import Console
from rich.markdown import Markdown
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

# ---------------------------------------------------------------------------
# Terminal front end. The chat logic lives in rp_core.Engine, which prints nothing.
# ---------------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] == "--group":
    if len(sys.argv) < 4:
        print("Usage: python roleplay.py --group <GroupName> <Char1> <Char2> ...")
        sys.exit(1)
    g_name = sys.argv[2]
    g_members = sys.argv[3:]
    os.makedirs(g_name, exist_ok=True)
    cfg_path = os.path.join(g_name, "group_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"members": g_members}, f, indent=2)
    print(f"Group '{g_name}' created successfully with members: {g_members}")
    sys.exit(0)

if len(sys.argv) < 2:
    console.print("[bold red]Error: Please specify a character to chat with![/bold red]")
    console.print("Usage: [cyan]python roleplay.py <CharacterFolder> [UserPersonaFolder][/cyan]")
    subdirs = [d for d in os.listdir(".") if os.path.isdir(d) and d not in [".git", "__pycache__", ".venv"]]
    if subdirs:
        console.print(f"Available folders: [green]{', '.join(subdirs)}[/green]")
    sys.exit(1)

ai_profile = sys.argv[1]
user_profile = sys.argv[2] if len(sys.argv) > 2 else None

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
    console.print(Panel(Text.from_markup(summary_text), title=f"[bold {eng.character_color}]{eng.character_name}[/bold {eng.character_color}]", title_align="left", border_style=eng.character_color, padding=(1, 2)))
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
        console.print(f"[bold {color}][{name}]:[/bold {color}] {text}")
    console.print("[dim]------------------------------------[/dim]\n")

console.print(f"\n[bold magenta]✨ Miso Chat Active: [{eng.user_display_name}] chatting with [{eng.character_name}] (Discovered Models: {len(eng.fallback_models)})! Type 'exit' to quit. ✨[/bold magenta]\n")

VERBOSE_MODE = False

while True:
    try:
        console.print(Rule(style="dim magenta"))
        console.print(f"[bold {eng.user_color}][{eng.user_display_name}][/bold {eng.user_color}] ", end="")
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

        if cmd.startswith("/sync"):
            extra_peer = user_input.strip()[5:].strip()
            console.print(Rule(style="dim cyan"))
            console.print(f"[bold cyan]🔄 Manually triggering local P2P Wi-Fi sync{' with ' + extra_peer if extra_peer else ''}... (OwO)[/bold cyan]")
            run_local_p2p_sync([extra_peer] if extra_peer else None)
            console.print("[bold green]✨ P2P sync completed! ✨[/bold green]")
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
                    console.print(f"[bold {eng.user_color}][{eng.user_display_name}]:[/bold {eng.user_color}] {text}")
                else:
                    console.print(f"[bold {eng.character_color}][{eng.character_name}]:[/bold {eng.character_color}] {text}")
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
            console.print(Panel(Text.from_markup(highlight_quotes(reply.display)), title=f"[bold {reply.color}]{reply.speaker}[/bold {reply.color}]", title_align="left", border_style=reply.color, padding=(0, 1)))

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
