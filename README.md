# Miso Chat

Miso Chat is a roleplay chat app. It talks to Gemini, with optional DeepSeek, local OpenAI-style server and Claude Code backends. It has two front ends:

- `roleplay.py`, a terminal chat for one character, or a group of characters.
- `web/`, a FastAPI web app for several accounts. Each account has its own chat history, memory and persona choice. The admin manages accounts, usage caps, token budgets, providers, character cards, icons and a stats page.

Miso Chat is the web version of [Character-Forge](https://github.com/wyn-cmd/character-forge), the terminal roleplay engine it grew out of. The character-folder format, memory banks, group chats and the terminal engine (`roleplay.py`) originate there. The web app, accounts, admin editor, icons and browser interface are new here.

This is a personal project. It is built for use on one machine or a private network. It is not hardened for the open internet. See Security below.

## Requirements

- Python 3.11 or newer. The code was tested with Python 3.11 on Windows.
- A Gemini API key. Optional: a DeepSeek key, a local server with an OpenAI-style `/v1/chat/completions` endpoint, or the `claude` CLI signed in to an account.

## Setup

    python -m venv .venv
    .venv\Scripts\activate        (Windows)
    source .venv/bin/activate     (macOS or Linux)
    pip install -r requirements.txt
    copy .env.example .env        (or: cp .env.example .env)

Open `.env` and set `GEMINI_API_KEY`. The app reads `.env` when it starts. A value that is already set in the environment wins over the file.

## Terminal chat


The second argument is the persona folder you speak as. Inside a chat:

- `/model` shows the models, `/model <name>` pins one, `/model auto` returns to the fallback chain.
- `/save` updates the memory bank now.
- `/back` removes the last exchange.
- `/history` prints the whole chat.
- `/verbose` toggles diagnostics.
- `/sync` and `/sync <address>` run the optional LAN sync described under Sync below.
- `/quit` or `/exit` ends the session and saves the memory bank.

## Web app

    python -m uvicorn web.app:make_app --factory --host 127.0.0.1 --port 8000

Open http://127.0.0.1:8000 in a browser. The first login is `admin` with the password `admin`. The app asks you to change it before anything else. Create other accounts from the admin page.

Run the web app from the project folder, because character folders are read relative to it.

### Appearance

Each browser can set its own highlight colour in the Settings window (the Settings button above the chat box). The colour drives the chat outline, buttons, links and the settings and admin text. It is saved in the browser's local storage, so it is not shared between accounts or devices.

The chat look (font, name badge and size) is in the same window. On phones, the Settings button opens a bottom sheet with these controls.

## Configuration

| Name | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for Gemini models. Read from `.env` or the environment. |
| `MISO_DATA_DIR` | Where `app.db`, `providers.json` and per-user chat state live. Default: `./data`. |
| `MISO_COOKIE_SECURE` | Set to `1` when the web app is served over HTTPS, so the login cookie is marked Secure. |

The older names `ROLEPLAY_DATA_DIR` and `ROLEPLAY_COOKIE_SECURE` are still read.

### Other providers

DeepSeek, a local OpenAI-style server and Claude Code are set up on the admin page under Model providers. Keys are write-only: the browser sees only a masked hint. They are stored in `data/providers.json`, with owner-only permissions where the system supports them.

Model ids for these providers carry a prefix: `deepseek:...`, `local:...` and `claude:...`. The Claude Code backend runs the `claude` CLI once per message, in print mode, with no tools.

### Models

The default fallback chain is set in `rp_core.py` (`ALLOWED_MODELS`, `DEFAULT_FALLBACKS`). Only ids in `ALLOWED_MODELS` are used. Provider model names change over time, so check them against the provider's documentation before you rely on them.

## Characters and personas

Each character is a folder in the project root.

- `character.md` holds the card. The first line is `Character Persona: <Name>`, and a line `Colour: <name or #hex>` sets the colour used for the name badge and bubble.
- `context.md` (optional) holds memories, places and situations, and is added to the prompt.
- `icon.png`, `icon.jpg` or `icon.webp` (optional) is shown beside the name. Without one, the app shows a coloured initial. Uploads in the web editor accept PNG, JPEG or WebP and are re-encoded to a WebP icon under 512 KB, cropped to a square.
- `wiki.md` (optional) is a lore file. When it exists, the model can ask for lines from it mid-scene with `[WIKI_SEARCH: keyword]`, and only the matching lines go into the prompt. The terminal chat and the web app both support this.
- `memory.json` and `memory.md` are written by the app as the chat runs. Do not edit them while a chat is open.

A group chat is a folder with a `group_config.json` that lists its members.



## Word substitutions

An optional `substitutions.json` in the project root maps a word or phrase to a replacement, for example `{"old word": "new word"}`. Matching is whole-word and longest phrase first, and the replacement is applied to your messages before they are sent.

## Sync (terminal only)

`roleplay.py` contains an optional peer-to-peer sync that copies character folders between two machines on the same local network. It listens on port 54322 and searches for peers on the local network. Peers can also be listed in `peers.json`, for example `["192.0.2.10"]`, or added at runtime with `/sync <address>`. Sync state files (`device_id.json`, `user_id.json`, `peers.json`, `*.p2pbak`) are per machine and git-ignored. The web app does not sync.

## Data and what is not included

`data/` holds the database, accounts, sessions, per-user chat history and memory, and provider keys. `.env` holds your Gemini key. Both are git-ignored in `.gitignore`. Keep them out of any repository or shared folder. The same applies to `memory.json`, `memory.md`, `facts.json`, `user.md` and the per-machine state files.

This repository ships only the example character and persona cards. Personal characters, their memories and chat history are not included.

## Security

- Use the web app on a private network, or behind a private tunnel or VPN. Set `MISO_COOKIE_SECURE=1` when you serve it over HTTPS.
- Change the admin password on first login. Use long, unique passwords for every account.
- `rp_core.py` sets the safety thresholds in `safety_settings()` to `BLOCK_NONE` for sexual content, harassment, hate speech and dangerous content. Review them before you use the app, and decide what your provider allows. You are responsible for the content you generate and send.
- Costs are billed by each provider. Claude Code uses the quota of the account signed in to the `claude` CLI.

## Checks

This copy does not include the automated test suite. A basic static check is:

    pip install pyflakes
    python -m pyflakes roleplay.py rp_core.py llm_backends.py dotenv_loader.py web

## Known limits

- Usage caps and token budgets are checked before each turn. A turn that starts under budget can finish over it.
- Token counts in the context marker are estimates, about four characters per token.
- Gemma 4 31B on the free tier has a small input limit. Group chats do not use it.
- The web app's browser tests have not been run in this copy. Check the layout on your own screens, especially the colour settings.

## Credits and licences

- Merriweather, in `web/static/fonts`, is licensed under the SIL Open Font License 1.1. See `web/static/fonts/OFL.txt`.
- No licence has been chosen for Miso Chat yet.
