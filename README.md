# Miso Chat

Miso Chat is a roleplay chat app. It talks to Gemini by default, and can also use DeepSeek, a local server that speaks the OpenAI chat format, or the `claude` CLI. It has two front ends:

- `roleplay.py`, a terminal chat with one character or a group.
- `web/`, a FastAPI web app for several accounts. Each account has its own chats, memory and persona. The admin manages accounts, usage caps, token budgets, providers, character cards and icons, and has a stats page.

This is a personal project, built for one machine or a private network. It is not hardened for the open internet. See Security below.

## Requirements

- Python 3.11 or newer. The code was tested with Python 3.11 on Windows.
- A Gemini API key. Optional: a DeepSeek key, a local OpenAI-style server, or the `claude` CLI signed in to an account.

## Setup

    python -m venv .venv
    .venv\Scripts\activate        (Windows)
    source .venv/bin/activate     (macOS or Linux)
    pip install -r requirements.txt
    copy .env.example .env        (or: cp .env.example .env)

Open `.env` and set `GEMINI_API_KEY`. The app reads `.env` on start-up. A value already set in the environment wins over the file.

## Terminal chat

    python roleplay.py Cliff
    python roleplay.py Cliff Cayprae
    python roleplay.py --group MyGroup Cliff Cayprae

The second name is the persona folder you speak as. Inside a chat:

- `/model` lists the models. `/model <name>` pins one, and `/model auto` goes back to the fallback chain.
- `/save` updates the memory bank now.
- `/back` removes the last exchange.
- `/history` prints the whole chat.
- `/sync` pulls chats from other machines on your network. See Peer sync below.
- `/verbose` toggles diagnostics.
- `/quit` or `/exit` ends the session and saves the memory bank.

## Web app

    python -m uvicorn web.app:make_app --factory --host 127.0.0.1 --port 8000

Open http://127.0.0.1:8000 in a browser. The first login is `admin` with the password `admin`, and the app asks you to change it before anything else. Create other accounts from the admin page.

Run the web app from the project folder, because character folders are read relative to it.

What the web app can do:

- Edit any saved message. Editing does not call the model.
- Regenerate a reply and swipe between the saved versions.
- Keep a rolling window of recent messages (1 to 100, set in Settings). Older messages are folded into a running summary during the memory refresh. The summary is shown in the Memory panel and can be edited there.
- Set a creativity (temperature) value for each character in Settings, from 0 to 1.5.
- Export a chat as Markdown or plain text.
- Turn on a character sandbox, a private folder the model can read and write through a few file tools. It is off by default. Turn it on by putting `{"enabled": true}` in a `sandbox.json` file in the character folder. Admins can browse sandboxes read-only.
- Switch between a dark theme (the default) and a light theme with the toggle in the header.

## Configuration

| Name | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for Gemini models. Read from `.env` or the environment. |
| `MISO_DATA_DIR` | Where `app.db`, `providers.json` and per-user chat state live. Default: `./data`. |
| `MISO_COOKIE_SECURE` | Set to `1` when the web app is served over HTTPS, so the login cookie is marked Secure. |

The older names `ROLEPLAY_DATA_DIR` and `ROLEPLAY_COOKIE_SECURE` are still read.

### Other providers

DeepSeek, a local OpenAI-style server and Claude Code are set up on the admin page, under Model providers. Keys are write-only: the browser sees a masked hint only. They are stored in `data/providers.json`, with owner-only permissions where the system supports them.

Model ids for these providers carry a prefix: `deepseek:...`, `local:...` and `claude:...`. The `claude:` prefix runs the `claude` CLI installed on this machine, using the account it is signed in to.

### Models

The Gemini models the app will use are listed in `ALLOWED_MODELS` in `rp_core.py`, and the default fallback chain is `DEFAULT_FALLBACKS`. Only ids in `ALLOWED_MODELS` are used. Provider model names change over time, so check them against the provider's documentation before you rely on them.

## Characters and personas

Each character is a folder in the project root.

- `character.md` holds the card. The first line is `Character Persona: <Name>`. A line `Colour: <name or #hex>` sets the colour of the name badge and bubble.
- `context.md` (optional) holds memories, places and situations, and is added to the prompt.
- `icon.png`, `icon.jpg` or `icon.webp` (optional) is shown beside the name. Without one, the app shows a coloured initial.
- Names can hold any characters, such as `Dr. Fox: Night Form`. Windows folder names cannot hold `< > : " / \\ | ? *` or `%`, so those are percent-encoded in the folder name: `Dr. Fox: Night Form` lives in the folder `Dr. Fox%3A Night Form`. The name shown in chat comes from the folder name, or from the `Character Persona:` line when there is one.
- `memory.json` and `memory.md` are written by the app as the chat runs. Do not edit them while a chat is open.

A group chat is a folder with a `group_config.json` that lists its members.

`personas.json` lists the folders you can speak as. A persona is never offered as a character to chat with. `Cayprae` is included as an example persona, and `Cliff` as an example character.

## Word substitutions

An optional `substitutions.json` in the project root maps a word or phrase to a replacement, for example `{"old word": "new word"}`. Matching is whole-word and longest phrase first. The replacement is applied to your messages before they are sent. This copy ships with an empty list, and the built-in default in `rp_core.py` (`DEFAULT_SUBSTITUTIONS`) is empty too.

## Peer sync

Sync lets you copy chats and character folders between two machines on the same local network. It is manual.

- The terminal client listens on port 54322 (TCP and UDP) while it runs, so the other machine can find it. Nothing syncs at start-up. Sync runs only when you type `/sync`.
- `/sync` looks for other copies on the local subnets and syncs with the ones it finds. Add addresses by hand in a `peers.json` file (a list of `"host"` or `"host:port"` entries) if broadcast is blocked, or type `/sync <address>`.
- Files are copied with a backup (`.p2pbak`) before they are overwritten.
- `peers.json` is a per-machine file. It is git-ignored and is not part of this repository.

Use sync only on a network you trust. It has no encryption and no login.

## Wiki lookup

A character can have a `wiki.md` file in its folder. When a message contains a tag `[WIKI_SEARCH: keyword]`, the app searches that file for the keyword and gives the relevant part to the model.

`scrape_wiki.py` can save a page as Markdown into a character folder:

    python scrape_wiki.py <url> <output_dir> <filename>

It sends the URL to the Jina Reader service (r.jina.ai) to get the page as text, so the page's content leaves your machine. Check the site's terms before you scrape it. Wiki files you make are not included in this repository.

## Data and what is not included

`data/` holds the database, accounts, sessions, per-user chat history and memory, and provider keys. `.env` holds your Gemini key. Both are git-ignored. Keep them out of any repository or shared folder.

The repository does not include personal character folders, memory files, wiki dumps, peer lists, usage trackers or your provider keys. Only the two example characters are included.

## Security

- Use the web app on a private network, or behind a private tunnel or VPN. Set `MISO_COOKIE_SECURE=1` when you serve it over HTTPS.
- Change the admin password on first login. Use long, unique passwords for every account.
- `rp_core.py` sets the safety thresholds in `safety_settings()` to `BLOCK_NONE` for sexual content, harassment, hate speech and dangerous content. Review them before you use the app, and check what your provider allows. You are responsible for the content you generate and send.
- Costs are billed by each provider. Claude Code uses the quota of the account signed in to the `claude` CLI.

## Checks

This repository does not include an automated test suite. A basic static check is:

    pip install pyflakes
    python -m pyflakes roleplay.py rp_core.py llm_backends.py dotenv_loader.py web

## Known limits

- Gemma 4 31B on the free tier has a small input limit (16K tokens a minute), so a large prompt can be refused. Use a billed key or a Flash-Lite model for long chats.
- The rolling summary folds in at most 60 messages per memory refresh. If more than 60 messages leave the window between refreshes, the oldest are skipped.

## Versioning and contributing

The version is in `VERSION`, and every release is listed in `CHANGELOG.md`. Each release is tagged on GitHub (for example `v1.1.0`).

## Credits and licences

- Miso Chat is released under the MIT licence. See `LICENSE`.
- Merriweather, Inter and JetBrains Mono are in `web/static/fonts`. Merriweather is under the SIL Open Font License 1.1 (`web/static/fonts/OFL.txt`). Inter and JetBrains Mono are under the same licence (`web/static/fonts/OFL-inter-jetbrains-mono.txt`).
