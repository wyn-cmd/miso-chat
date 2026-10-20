# Changelog

Notable changes to Miso Chat, newest first. Version numbers follow MAJOR.MINOR.PATCH. The current version is in `VERSION`.

## 1.3.1-beta - 2026-10-20

A beta that adds character backgrounds, face icons, appearance sliders and a settings grid. It keeps the 1.3 protocol, so terminal clients from 1.3.0 still connect.

### Added

- Up to 10 backgrounds per character. The character editor adds and removes them one at a time. Export and import carry all of them.
- A chat picks one of a character's backgrounds at random each time it opens.
- Home cards and the character info card cycle through the backgrounds at random. Each change waits 5 to 8 seconds, then fades to the next picture. Reduced-motion settings keep one still picture.
- Shared default backgrounds in `web/static/defaults/`, for characters with no backgrounds of their own. They cycle the same way.
- A character with no picture shows a face in its colour, instead of the first letter of its name. The character editor shows the same face as its preview.
- Appearance has a background brightness slider (darker or lighter) and a chat bubble opacity slider. Each account keeps its own settings.
- Settings is a grid of cards, one for each category. Admins also see an Admin card.
- README screenshots, using made-up characters with generated art and chats.

### Changed

- Chat look and creativity sit under Appearance and Character. Persona is a card of its own.
- On phones the Undo button sits beside the Tree button.
- The single-upload background route is replaced by a route that adds one background at a time. Existing `background.jpg` files still show.

### Upgrading

Follow `UPDATE.txt`. Keep `.env` safe: it holds `MISO_SECRET_KEY`, and two-factor secrets cannot be read without it.

## 1.3.0 - 2026-10-20

This release adds two-factor sign-in, encrypted backups made from the admin page, a closer match to SillyTavern for the Mind Palace, and the character changes below. It also includes everything from 1.3.0-beta and 1.3.0-beta-2: the terminal client, character pictures in the terminal, the version check and the sign-in protection.

### Added

- Two-factor sign-in with an authenticator app, using the 6-digit code that changes every 30 seconds. Any account can turn it on in Settings. Once it is on, each sign-in asks for the code after the password.
- Admins can require two-factor sign-in on any account. An account that is required but has not yet set up two-factor sign-in goes to a set-up page straight after its password. The page shows a QR code and the key to add to the app, then asks for a code from the app. Until that code is accepted, nothing else in the app is available. The set-up page stays open for 15 minutes.
- An "enable 2FA" button on the admin's own row of the users table, which opens the same set-up window as Settings. Other admins' rows do not show the button.
- Admins can reset two-factor sign-in on an account that has lost its phone. The account then sets up a new code at its next sign-in, and all its sessions end.
- Two-factor sign-in in the terminal client. The first sign-in with it required shows the key once, grouped in fours, because a terminal cannot show the QR code.
- Encrypted backups, made from the Backups section of the admin page. Only admins with two-factor sign-in on can make one. The first admin sets a backup password of at least 12 characters, which is stored only as a check value. A backup is one encrypted `.mbk` file. A data backup holds the data folder (accounts, chats, memory, characters and groups), the server settings and `.env`. A full backup also holds the server code. Backups are never scheduled. Another admin who downloads a backup cannot read it without the password.
- `restore_backup.py` restores a backup into an empty folder: `python restore_backup.py BACKUP_FILE TARGET_FOLDER`.
- Mind Palace (database searching) is closer to SillyTavern World Info. An entry can have secondary keys, combined with AND or NOT logic (and any, and all, not any, not all). An entry can be always on, can be switched off, and has its own scan depth (1 to 30 messages) and insertion order. A character has a budget of 500 to 12,000 characters, 3,000 by default, and entries can optionally trigger other entries (recursion). A character can have up to 60 entries, and up to 24 are matched in one turn.
- Any signed-in account can create a character. The account that creates it becomes that character's creator.
- Creators can edit their own characters, upload a background and edit the character's starting Mind Palace.
- The new-character form has a background picture option. The picture is uploaded when the character is created.
- Mind Palace in Settings. Each account has its own entries for each character. The first time an account chats with a character, it receives a copy of that character's starting palace. Changes apply from the next message.
- Admins can read another account's Mind Palace for a character from the character editor. They cannot change it.
- Search results show 50 characters per page, with Previous and Next buttons when there are more.
- The character info card (the i icon) uses the character's background picture, with a gradient that is darker at the bottom so the text stays readable.

### Changed

- A correct password no longer resets the wrong-code count. Lock counts clear only when a sign-in is finished.
- Requiring two-factor sign-in on an account that has not finished setting it up ends that account's current sessions, so the account must finish the set-up page first.
- The Mind Palace editor saves the recursion setting, the budget and each entry's options.
- Characters are ordered by recommendation. Characters that share tags with the ones an account chats with come first, then the most used across all accounts, then by name. The search page and the home page use the same order.
- The chat sidebar lists only the characters an account has chatted with. A character appears once it has at least one saved message, on the main chat or on a branch. A new account's sidebar starts empty. Groups are listed as before.
- The sidebar refreshes after each message, so a first message adds its character straight away. A new account with no open character no longer shows an empty chat window.
- Only admins can change characters made as admin. Characters from before creator tracking count as admin's. Admins cannot change a character a user created.
- Only admins can set the sandbox option or keep a creator name from an imported zip. Any other account that imports a character becomes its creator.
- Group chats still read the character's starting Mind Palace, which is shared by everyone in the group.

### Security

- Two-factor secrets are encrypted at rest with AES-GCM, under a key kept in `MISO_SECRET_KEY`. The server creates that key in `.env` on first start. Each secret is bound to its account. A code that has already been used is refused, so a copied code cannot be replayed.
- Backups are encrypted with AES-GCM under a key derived from the backup password with scrypt.
- The sign-in lockout, the per-address block, the secure cookie and the 10-character minimum password from 1.3.0-beta-2 are unchanged.

### Upgrading

Follow `UPDATE.txt`. Keep `.env` safe: it holds `MISO_SECRET_KEY`, and two-factor secrets cannot be read without it.

## 1.3.0-beta-2 - 2026-10-20

### Security

- Sign-in lockout per username, kept in the database. It counts failures whether or not the account exists, so a refusal never shows which names are real. 10 failures lock for 15 minutes; 5 more lock for 30 minutes, then 1, 2 and 4 hours. 35 failures with no successful sign-in lock the account until an admin unlocks it. A locked name is refused before its password is checked. Each address is counted separately too: 15 consecutive wrong guesses from one address block that address for that account until an admin unlocks it. Other addresses are not affected. The admin page shows locks and can clear them, and `unlock_account.py` clears one from the server.
- Password minimum is 10 characters, up from 8. Existing passwords still work; the minimum applies when a password is set.
- Password hashes use scrypt at cost 2^16, up from 2^14. Old hashes still sign in and are upgraded at the next sign-in.
- The session cookie is marked Secure by default. Plain-http setups must set `MISO_COOKIE_SECURE=0`.
- The terminal client refuses a redirect to a non-https address.

## 1.3.0-beta - 2026-10-20

This beta adds a terminal client for Miso Chat, with character pictures drawn in the terminal. It is for testing on a server before the 1.3.0 release.

### Added

- A terminal client, `client/miso_remote.py`. It logs in to a running server and chats with characters and groups from a terminal. It uses only the Python standard library. Italic and bold text show where marked, and each character's reply sits in a box in that character's colour.
- Character pictures in the terminal client. The client downloads a character's picture, draws it with chafa in colour braille, and keeps the drawing in a cache. `/portrait` shows it again during a chat. Pictures need chafa on the computer that runs the client.
- The server reports its version at `/api/version`. The terminal client checks it before login and connects only when the major and minor version match, so 1.3.0 and 1.3.4 work together. Older servers that do not report a version are refused.
- A download link for the terminal client, in the sidebar under Account and in Settings.

### Upgrading

Follow `UPDATE.txt`. The zip adds the `client/` folder, which the download link needs. No data changes are needed.

## 1.2.1 - 2026-10-20

This release adds group chats and a branch tree view, and changes how chat backgrounds and message bubbles look.

### Added

- Group chats. Any account can create one from the chat sidebar, with two to eight characters. A group is private to the account that made it: other accounts cannot see or change it. An account can have up to 20 groups.
- A director picks the speakers for each group turn. It reads the message, the chat history, the summary, the memory and the member notes, then returns the speakers in order. Each chosen member replies in that order. If the director cannot be used, one random member replies and the note in the chat says why.
- Per-member notes for each group. Notes are private to the account. The director and the noted member both read them.
- A branch tree view, opened from the Tree button in the chat. Each account sees the tree for its own chats. Admins can open any account's tree, read-only.

### Changed

- The chat background covers the whole page, behind the sidebar and the header, not only the chat column. Dark mode darkens it. Light mode shows the image as uploaded.
- In dark mode the sidebar is nearly opaque, so the background does not show through it.
- Your messages shrink to fit short text and stay on the right. Character messages sit on the left at the same width.
- The director uses the same model as the group members, the model picked in the account's settings.
- New background uploads keep their original colours. Backgrounds uploaded before this release stay as they are until you upload them again.

### Upgrading

Follow `UPDATE.txt`. No data changes are needed. Group and note data is written under each account's folder in `data/`, which the update does not touch.

## 1.2.0 - 2026-10-19

This release adds a home page, chat branches, character import and export, drafting a character from a file, and the Mind Palace.

### Added

- A home page with character cards, a search box, a tag filter and recommendations based on the tags of the characters you chat with.
- Chat branches. Each saved message has a Branch button that copies the chat up to that message into a new branch. You can switch between branches and delete one. A chat can have up to 12 branches, and groups work the same way.
- Character import and export as zip files. The export can include this account's chat history and memory.
- Chat backgrounds. An admin uploads one per character. It is darkened and shown on the character's home card and behind its chat.
- Creators. A new character is credited to the account that created it. Existing characters are credited to admin.
- Drafting a character from a text, Markdown, Word, PDF or screenshot file. Text is read directly. Pictures and scanned pages are read with Tesseract OCR. The draft fills the new-character form for you to check. Pictures never become the profile or background picture.
- The Mind Palace. Keyword entries join the prompt only when one of their keys appears in the recent chat.
- Admin switches for the memory bank and the Mind Palace, both on by default.
- A reply length setting. Admins set the range, and each account picks a target inside it.
- Squircle avatars, with a circle option in the chat Settings.
- A dice button on Auto-fill that shows a random face each time.

### Changed

- The chat window and message bubbles are more transparent, and the bubbles have a shadow.
- The top bar on every page stays on one row, including on phones.
- requirements.txt adds pymupdf and python-docx.

### Upgrading

Run `pip install -r requirements.txt`. Tesseract is needed only for drafting from screenshots and scanned pages. The README has install steps for Windows and Linux.

## 1.1.1 - 2026-10-19

A small release. Apart from the wiki removal, nothing changes what the app does.

### Removed

- Wiki lookup: the `[WIKI_SEARCH]` tag, the `wiki.md` file and `scrape_wiki.py`.

### Changed

- The README's word substitutions section has its examples again.
- Dead code is gone: an unused colour check in the character editor and an unused model list in the engine.
- The terminal client uses the chat helpers in `rp_core.py` rather than its own copies, so the two can't drift apart.

## 1.1.0 - 2026-10-19

This release is mostly about the chat itself: replies can be regenerated and compared, saved messages can be fixed, and the memory is easier to see and shape.

### Added

- Regenerate a reply, up to 30 times per message, and swipe between the saved versions. The original reply is kept too, so a message can have 31 versions.
- Edit any saved message from the web app. Editing does not call the model.
- A Memory panel that shows what the character remembers. You can edit it, pin facts so the automatic refresh leaves them alone, and refresh from the chat. Refresh is the only button in the panel that calls the model.
- A running summary. When the memory refresh runs, messages that have left the rolling window are folded into a short summary that goes into the prompt. You can read and correct it in the Memory panel.
- A rolling window slider in Settings, from 1 to 100 messages. The default is 25 for one-to-one chats and 40 for groups.
- A creativity slider for each character, from 0 to 1.5, in Settings. The defaults are 0.85 for one-to-one chats and 0.7 for groups. The Default button clears your setting.
- Export a chat as Markdown or plain text from Settings.
- Character sandboxes: a private folder per account and character that the model can reach through a few file tools. It stays off until a `sandbox.json` with `{"enabled": true}` is added to the character folder. Admins can browse sandboxes, read-only.
- A light theme. The toggle is in the header, and in the top-right corner of the login page. Dark is still the default.
- A new logo, used for the favicon, the app icons and the header.

### Changed

- The interface uses a darker, blue-violet palette with rounded cards, a blurred header and gradient buttons.
- Inter and JetBrains Mono are bundled with the app, so the page no longer loads fonts from another server.
- The default highlight colour is cyan (blue in light mode). A colour picked in Settings still overrides it.
- In dark mode, sidebar characters are dark cards with a light outline.
- The Send button is now an up arrow.
- On phones, Roll is no longer in the header. The Memory button moves into the Settings sheet on phones. Desktop keeps Roll and Memory in the chat bar.

## 1.0.0 - 2026-10-15

The first public version, merged from pull request #1. It had the web app and the terminal chat, with Gemini as the main backend and optional DeepSeek, local OpenAI-style server and Claude Code backends. It had per-account chat history, memory and persona choice, the admin page with usage caps, token budgets, providers, character cards and icons, and a stats page. It also added auto-fill and roll with a reworked phone chat header, word substitutions, manual peer sync on a local network, and wiki lookup from a character's `wiki.md`.

This version has no git tag. Its state is the merge commit of pull request #1.
