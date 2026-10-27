# Changelog

Notable changes to Miso Chat, newest first. Version numbers follow MAJOR.MINOR.PATCH. The current version is in `VERSION`.

## 1.5.2-beta-1 - 2026-10-28

A read-aloud, search and settings cleanup release, built on 1.5.1. The server's endpoints and data layout are the same as 1.5.1, and the terminal client is 1.5.2-beta-1 with the same code.

### Added
- **Read aloud** (chat and Settings, Voice & audio): a speaker button under each character reply reads it with the browser's speech synthesis. The browser remembers a voice, rate, pitch and volume, and a different voice for each character or group. Auto-read is off by default.
- **Voice & audio settings**: the panel lists the voices installed in the browser, keeps a saved voice when it is unavailable, and shows a status line when speech is unsupported or has no voices. Test and stop buttons are included.
- **Scenario library** (Settings, Scenarios): each account keeps its own opening messages for chats, with three to start from. A new chat can offer one chosen scenario, one at random, or none; the opening appears in the chat window with a Start button and is only sent when pressed.
- **Global search** (sidebar): searches characters, worlds, Mind Palace entries and saved chats. A result opens the chat or the settings panel that owns the match.

### Changed
- **Default creativity** is now 0.95 for both one-to-one chats and groups. Per-character and per-group settings still override it, and the Default button clears them.
- **Settings** now has one Story & memory card for Worlds, Mind Palace and Dreams, in place of three separate cards. Each subpanel returns to Story & memory.
- **Command help** now lists the commands that work in the web chat and notes that `/verbose`, `/exit`, `/quit` and `/quit-clear` are terminal-only. `/exit` no longer claims to close the web chat.
- The settings grid keeps its 12 cards in four rows of three on desktop after the new Voice & audio card and the Story & memory merge.

### Removed
- The separate Worlds, Mind Palace and Dreams cards from the settings grid. Their panels are still available under Story & memory.

### Fixed
- `/help` and other command notices no longer disappear when the command reloads the chat history.
- `/undo` now reloads the chat and then shows its command notice.

### Known issues
- Read aloud depends on the voices installed in the browser or operating system. The server does not send audio, and a browser with no voices shows a status message instead.

## 1.5.1 - 2026-10-27

A look, icon and speed release, built on 1.5.0. The server's endpoints and data layout are the same as 1.5.0, and the terminal client is 1.5.1 with the same code.

### Added
- **Scenario library** (Settings, Scenarios): each account keeps its own opening messages for chats, with three to start from. A new chat can offer one chosen scenario, one at random, or none; the opening appears in the chat window with a Start button and is only sent when pressed.
- **Dusk look** (Settings, Appearance, Theme): a slate ground with warm bone text and a periwinkle accent, with a light variant for the day.
- **Video backgrounds**: an MP4 or MOV background for a character or account is converted to WebM and MP4 with no audio, and a poster frame is kept for the first paint and for reduced motion. The page offers both files, so a phone that cannot play VP9 uses the MP4. The blur and dimming are applied once, on the server. This needs ffmpeg and ffprobe on the server; without them a video upload is refused with a message.
- **GIF backgrounds**: a character, group or account background can be a GIF, up to 10 MB. An animated one is stored as uploaded and keeps moving; a still one is saved as PNG like any other picture.
- **Account profile pictures and backgrounds**: each account can set a picture and a background, from its profile page.
- **Animated GIF pictures** stay animated. They are stored as uploaded and served as image/gif, so a browser shows every frame.
- **Background circles**: a character with no pictures shows drifting circles behind the chat. Each home card without pictures has its own set. Each circle has its own pace and start point. When mood and attitude are on, the circles warm with affection and turn greener with energy.
- **App icon**: a panda in the Moonlit colours, used for the browser tab, the home-screen icon and the page header.

### Changed
- Picture backgrounds are saved as PNG. Pictures saved before this release stay JPEG and are still read.
- The colour look (sepia, saturation and dimming) is applied once, when a picture or video is saved. The page applies only the contrast step.
- In light mode, card and chat pictures and videos are shown with no shade over them.
- Background videos play only while they are on screen and the tab is visible. They pause otherwise, and profile videos pause with the tab. A browser that refuses to start one at first, which an iPhone in Low Power Mode does even for a muted video, is asked again on the first touch or click.
- A background video saved before this release has no MP4 copy yet. Its MP4 is made from the stored WebM the first time a browser asks for it, and kept from then on.
- The header background is solid, not blurred.
- The Classic look's background glow is a fixed layer behind the page, so it is not repainted on every scroll.
- Scripts and styles from `/static/` are sent gzip-compressed. Chat replies are not compressed, so they still stream as they are written.
- The character list is kept until a character, group or persona file changes, so the pages do not reread every folder on each request.
- The keyboard-fit check runs only while the message box has focus.
- Video backgrounds, picture backgrounds and profile backgrounds are not re-processed. A background saved before this release keeps its old file: its colour look and blur are not applied, so it shows as it is. Upload it again to get the new look.
- The manifest's theme colours match the Moonlit ground.

### Removed
- The Spore look. A browser that had chosen Spore now gets Moonlit.
- The three Spore default background pictures.
- The Spore screenshots from `docs/screenshots`.

### Known issues
- A long chat reply re-renders the whole message on each frame while it streams. This is unchanged from 1.5.0.

## 1.5.0 - 2026-10-26

A feature and visual release. It adds worlds for stories, novels, narrative tension and a second reading look. It keeps long chats cheaper to run without losing the thread, through clipping and quenching that work with the provider's prompt cache. It also hardens sign-in for internet-facing use. The server's endpoints and data layout are the same as 1.4.1, so chats, characters, accounts and the switches from 1.4.1 carry over. Everyone is signed out once, because the login cookie has a new name, and the terminal client must be the 1.5.0 file.

### Added
- **Worlds** (Settings, Worlds): up to 20 per account, each with a summary and seven sections: locations, magic systems, rules, civilisations, factions, technology and other details. A chat or novel uses its own world, the account default, or none.
- **Token counts for worlds**: each entry and section shows its estimated tokens while you write. A save over a limit is refused with a message that names the entry or section.
- **World formatting**: bullets and `[NEW]` tags are removed on save, and numbers are kept, so numbered rules stay citable.
- **Novels**: a narrator tells a story for a cast of solo characters, in one world or none. Each novel has its own chat, memory and tensions, visible only to its owner.
- **Narrative tensions**: open story threads stay in the prompt until the story resolves them. A chat chooses whether they are read on the nightly dream, on each memory save, or not at all.
- **Maker page**: creates novels and group chats, with a quick form for a new world.
- **Soft Shadows look**: a reading view that sets the chat as a written page. Every control stays where it was.
- **Clipping** (Settings, Chat history, on by default): older messages are cut to about 550 characters, at a word break where one is near.
- **Next-prompt marker**: shows the estimated prompt size, the part the provider can reuse from its cache, the new part, and the tokens saved by quenching and clipping.
- **Password checks**: common passwords, passwords equal to the username, and runs of one or two characters are refused wherever a password is set or changed.

### Changed
- Moonlit is the default look. Soft Shadows, Spore (1.5.0) and Hyacinth (1.4.1) are the other choices under Settings, Appearance, Theme. The reading look was called Folio while it was in testing; its saved choices still apply.
- The default backgrounds are a set per look (`moon`, `folio`, `spore` and `classic`) in place of the old default images.
- The chat window is at least the size set under Chat history (25 messages for a 1:1 chat, 40 for a group) and up to nine messages more. Its start moves in ten-message steps, which line up with the clip boundary, so the same history stays cached for ten turns at a time.
- The running summary covers each message as it is clipped, and reads each one to 550 characters. Before, it covered only messages that had left the window, and read them to 300 characters.
- Quenching's second stage works only on messages older than the clip boundary, and its result is kept until those messages change. It is on by default in group chats for an account that has never set it. An explicit off applies everywhere.
- The memory bank refreshes every 20 messages, up from 18.
- A group runs at most two extra replies a turn after the director's picks, down from four. A group with one member skips the director, since the choice is forced.
- Group replies no longer repeat the user's line, which is already the newest message each member reads. Each member's card, note and examples now come in the message after the shared history, and every member shares one system text, so the history can be reused across members. This changes how members are prompted, so check a few group replies after updating.
- The director reads each member as one line of its card, and no longer reads the memory snippet. It reads the last 20 messages for the first pick and the last 8 for the next speaker, and the recalled material comes after the history.
- Sign-in cookies are named `__Host-rp_session` when served over HTTPS, so no other subdomain can set or replace them. A sign-in request that does not say whether to remember the device gets a session cookie, not a 30-day one. The sign-in page's own checkbox is unchanged.
- The terminal client is 1.5.0.

### Security
- A fresh install no longer creates `admin` with the password `admin`. It generates a random one-time password and prints it once to the server log. Sign in with it and change it.
- Sign-in hardening for internet-facing use. Throttle records for quiet names and addresses are removed after 30 days, and each table is capped at 50,000 rows, so random names cannot grow the database. At most four password checks run at once. A request body is limited as it arrives, not only when it declares its length. A password change no longer blocks the server while the current password is checked.
- Over HTTPS the server sends a one-year Strict-Transport-Security header, X-Frame-Options DENY, and a Permissions-Policy that turns off camera, microphone, geolocation and payment, which the app does not use.

### Fixed
- Signing out with no session cookie, such as an expired one, returns a normal response rather than a server error.
- Clipped messages are covered by the running summary and the episodes. Before, a message was not summarised until it had left the window, so its detail was missing from the summary while it stayed in the window.
- A group member's reply no longer repeats the user's message.
- Worlds: an entry or section over its limit is refused with a message, not cut silently.
- Unused prompt text and a helper were removed from the server code.

### Notes
- The theme choice, the highlight colour and the light or dark setting are kept per browser, not per account.
- Known: at 1440 px the gallery fits four 242 px columns, which leaves about 96 px for the text inside each card, so long headlines wrap one word per line and the cards grow tall. The grid width is the app's existing rule, and 1.4.1 has the same effect.
- Prompt-cache hits depend on the provider. The app counts what the provider reports, so the stats page shows the real cached share.

## 1.4.1 - 2026-10-25

A feature release on the 1.4 line. It adds mood and relationship state, a memory graph written by dreams, group Mind Palace entries and group auto-reply, example messages on the character card, deleting messages from a chat, and two opt-in ways to spend fewer tokens. Every new switch starts off. Back up before updating, and take the 1.4.1 terminal client. See UPDATE.txt.

### Added
- Mood. A solo chat can now carry energy, affinity and an emotion that moves between calm, warm, tired and lonely, with margins on the way back so it does not flicker. Energy only falls once one unbroken stretch of talk passes the lite allowance, and it returns only in silence, so free and lite accounts are never left exhausted. Off by default (admin switch), and off means the block is not drawn and no model call is made.
- Relationship. Affection and trust run from 0 to 100 across five levels, from stranger to married. They move on bonding events with a three-exchange cooldown and a cap of six a turn, and ease back after a long stretch without one, counted in exchanges rather than in time. Married comes only from a proposal at partner level. Off by default (admin switch).
- Bars instead of bare numbers. Mood and relationship are drawn as progress bars with the value beside them, and the same bars carry the Memory retention report.
- Admin editing of those values. An admin can set energy, affinity and the emotion, and affection and trust, by hand from the chat's settings, for their own account's row. Any other account gets 403 from the server, not just a hidden control.
- Event memory graph. The nightly dream now also writes dated events, each with an actor, what happened, the belief it left and its consequence, plus a small set of evolving lines that change as the chat does. Later turns recall the events whose words overlap the newest messages. Capped at 200 events and 12 evolving lines. Off by default (admin switch).
- Group Mind Palace. A group chat now has its own palace per account, and matched entries reach every member's reply and the director.
- Group auto-reply. After the director picks the first speakers, each reply may draw one more speaker, up to four extra replies a turn, and no member gives more than two in a row. Always on for group chats; there is no per-group switch.
- Example messages. The character editor has a section for up to 12 exchanges showing how the character speaks, each with a sample user line and the character's reply. The block sits directly after the card in the system prompt, is never sent as chat history, and is capped at 6000 characters so it cannot crowd the card. It is carried in character export and import, and a SillyTavern card's example dialogue is read into the section instead of staying in the card text.
- Deleting messages. A trash button beside Regenerate removes the last message, and Select turns on pick mode, which puts a checkbox on every message and a bar with All, None, Delete N and Cancel. Deleting shifts the running summary and the episodes back to the first removed message, so later messages are reread under the right position. Up to 500 messages at a time. Undo still removes a whole turn.
- Near-lossless token quenching, a per-account preference in Settings, Account, off by default. Two stages: the existing safe-word and whitespace stripping runs first, then an entropy pass removes the least informative sentences from the older part of the history only. The newest four messages, every message the account wrote, and every sentence carrying a figure, a proper noun or a quotation are left whole. A removal is accepted only while at least 92 percent of the window's distinct content words survive and no more than 35 percent of its characters are gone, so the pass stops before the text degrades. The context marker reports how much it saved. Near-lossless, not lossless: wording is bounded, meaning is not proven.
- Learned memory retention, an admin switch, off by default. Memories the character wrote for itself, meaning automatic Mind Palace entries and episodes, are scored by what happened afterwards: a correction counts against them, appreciation and continued conversation count for them. A memory that keeps failing to help decays faster than one that keeps helping. Rows drop after two retrievals below the floor, or when never retrieved within 24 turns. Entries the user wrote are never scored, weighted or dropped, and a learned score can only demote a memory, never outrank something the user wrote. The settings page shows the top items as bars.

### Changed
- The history window now starts on multiples of eight messages instead of sliding one message at a time, so the prefix repeats for several turns and the provider's prompt cache can reuse it. The window is therefore 25 to 32 messages, not exactly 25.
- The prompt is ordered from least to most often changed: character card, example messages, instructions, reply length, persona, context, pinned facts, memory bank, summary. Mood, relationship, palace, episode and event notes are sent with the newest user message and are never saved to the chat.
- Group chats can carry their own Mind Palace entries, and the terminal client's /memory-palace help now says so.
- The terminal client is 1.4.1. The server still accepts clients with the same major and minor version, so a 1.4.0 client keeps working.

### Fixed
- Deleting a reply now drops that exchange's saved alternatives, which would otherwise describe a chat that no longer exists, and a chat that begins on a reply is sent as a window that starts with the user's turn.

## 1.4.0 - 2026-10-24

The first release of 1.4. It brings together the three betas and the features added since. Upgrading from 1.3.1 needs a backup first, and the terminal client must be the 1.4.0 file. See UPDATE.txt.

### Added
- Memory that stays. The memory bank is updated by changes (added, corrected and removed lines) rather than rewritten, so a refresh cannot silently drop facts it did not mention. Pinned facts have their own card in Settings and are never changed by a refresh.
- Episodic memory. Earlier moments of a chat, cut into short episodes when they leave the recent window, come back when their keys or, failing that, their meaning comes up again. Each character can switch it off in its Mind Palace, and an admin can switch it off for the whole server.
- Automatic Mind Palace entries. A character can add up to three suggested entries every few turns, keeping the one that comes up most, up to a limit that replaces entries when full, and can correct its own automatic entries. Off by default, and the entries are marked as automatic.
- Autonomous dreaming. In a character's quiet hours it tidies its memory bank, links facts, and drops automatic palace entries that repeat or contradict others. Off by default. Admins set the server's cap on dream model calls per day and how many days apart dreams run. Dream calls are not counted against any user's requests or tokens.
- Scheduled check-ins. A character can message its user once a day, at a set time or at a random time in a range, marked as a check-in. A red count shows missed ones on the sidebar and home cards.
- Budget alerts. An amber warning at 80% of a daily, weekly or monthly token window and a red one at 100%, shown in the chat and optionally posted to an admin webhook.
- Chat export as EPUB and PDF, next to Markdown, text and JSON.
- Importing SillyTavern-style character cards (JSON, or PNG with the card inside, in the v1, v2 and v3 layouts) into the new-character form. The picture and the lorebook are carried over.
- Group chat pictures and backgrounds, set by the owner of a private group. Group chats can be renamed and their members and notes changed without losing either.
- Admin switches for episodic memory and dreaming, and a Mind Palace setting for automatic entries.

### Changed
- Mind Palace whole-word keys match common English endings: plurals, past and -ing forms, -er nouns and the spelling changes they need (cat and cats, love and loved, stop and stopped, city and cities). Irregular forms can be written as alternatives in one key, such as `mouse|mice`.
- Terminal client messages have two blank lines between them, and the character's picture is drawn larger, up to 164 columns and 66 rows.
- The terminal client is 1.4.0. The server still accepts clients with the same major and minor version.

### Fixed
- The terminal prompt no longer flickers while typing. Each keystroke redraws the prompt box in one write, with the cursor hidden during the redraw.
- A memory update that removes a duplicate fact keeps the fact. A removal now removes one line at a time.

## 1.4.0-beta-3 - 2026-10-23

A third beta of 1.4. It is not a release, and it still needs a live test with a model key.

### Added
- Budget alerts. When a daily, weekly or monthly token window reaches 80% of its limit, the top bar's token meter turns amber and a warning appears above the chat: "You have 20% of your daily token budget left", with the tokens left and when it resets. At 100% the warning turns red and says new messages are blocked until the window resets. The warning can be closed until the page is reloaded.
- Budget alert webhook. The admin Limits page has an optional webhook address. When an account reaches 80% or 100% of a window, the server posts a JSON message to it, once per window per level. The body has a text field and a content field, so Slack-style and Discord-style receivers can both read it.
- Chat export as EPUB and PDF, from Settings, Export, next to Markdown, text and JSON. Both are built on the server. The EPUB holds the chat as one chapter, and the PDF is A4 with the text flowed across pages.
- Pinned facts have their own card in Settings. They are sent with every reply, after the memory bank and before the running summary, so the character never forgets them, and they work for group chats too. Because they sit before the summary, they stay in the cached part of the prompt. Editing them rebuilds the cache from that point.
- Scheduled check-ins. In Settings, Check-in, each character can send the user one message a day. It is off by default. A check-in is either at a set time or at a random time within a range, in the time zone saved with the schedule. It asks how the user is and picks up from the earlier chat, and it is saved in the chat marked as a check-in. Missed check-ins show as a red count on the character's sidebar button and home card. Opening the chat marks them read. If the server is not running at the chosen time, that day's check-in is skipped. The schedule runs inside the server, with no cron job.
- Group chats can have a background. The owner of a private group sets one from the group's dialog, and it replaces the old one. Shared group chats show a background when one has been added to their folder.

### Changed
- Message spacing in the terminal client: each message is followed by two blank lines instead of one, so messages stand apart.
- The terminal client's character picture is drawn larger: the chafa box is capped at 164 columns and 66 rows.
- Terminal client is 1.4.0-beta-3. The server still accepts it because the major.minor is still 1.4.

### Fixed
- The terminal prompt no longer flickers while typing. Each keystroke redraws the prompt box in a single write, with the cursor hidden during the redraw, instead of clearing the box and drawing it in two steps.

## 1.4.0-beta-2 - 2026-10-22

A second beta of 1.4. It is not a release, and it still needs a live test with a model key.

### Added
- Account profile page, opened from Settings, Account, View profile. It holds an About you field and a profile background, which is checked and downsized on upload. Backgrounds are never read by OCR and are never used as the profile picture.
- Creator handles on character cards link to the creator's profile. Admin accounts have no profile, so their handle stays plain text.
- Token usage pop-up now shows each window as a progress bar and percentage, with the limit it is measured against.
- Admin Users and Tiers tables show tokens used against each limit, plus the daily and weekly percentages per tier.
- Admin stats split token use into cached input, uncached input and output, with the model time spent, and add a DeepSeek cost estimate per model in USD or CNY. Rates are published DeepSeek rates per million tokens, mapped onto this server's model ids, and leave out off-peak discounts, so the figure is an estimate.
- Chats can be exported as JSON, from Settings, Export, as well as Markdown and text. The JSON holds the character, the export time and every message.
- Terminal client: /characters switches chats from inside a chat, /autofill puts a server-drafted message in the prompt for editing, /usage shows requests and every token window, /message-length sets the reply length, /memory-palace adds a Mind Palace entry, and /settings-usage-bar picks daily, weekly or monthly for the prompt bar (daily by default).
- The terminal client shows one * per password character typed, and falls back to a hidden prompt when input is piped.

### Changed
- The token bar on desktop sits beside the requests counter under the prompt.
- The terminal client has a prompt fixed at the bottom of the window, in the manner of a coding agent: the chat scrolls above it, and the status line under it shows the model, the clock, a bar for the token budget closest to its limit, and an estimate of the next prompt that follows what is typed. A chat opens with the character's picture and the last two messages.
- The terminal prompt is a rounded box: the character name in the top border, the accent ❯ and typed line inside, and the status row in the bottom border. Messages in the chat are drawn as a coloured ● with the speaker's name beside it, with no box borders.
- The terminal's token bar follows the daily window unless /settings-usage-bar picks another, and uses █ for filled cells and ░ for empty ones so the bar is one height.
- Terminal client pictures are drawn larger: the chafa box is capped at 140 columns and 56 rows, and leaves 8 rows for the heading, the last messages and the prompt, so a picture fills most of the window above the prompt with more definition.
- Terminal client is 1.4.0-beta-2. The server still accepts it because the major.minor is still 1.4.

## 1.4.0-beta - 2026-10-21

A beta of the next minor version. It is not a release, and it still needs a live test with a model key.

### Added
- Token tiers: free (1M a month), lite (10M), standard (20M) and pro (100M). Admins have no tier and no cap. An admin Tiers panel sets the monthly cap, the allowed providers and the blocked models for each tier.
- Daily and weekly token limits on top of the monthly cap. Daily defaults to 10% of the monthly limit and weekly to 20%. Admins set these percentages per tier on the Tiers panel, and set a day, week or month limit for any account on the Users panel. A blank box uses the tier figure.
- A token bar under the prompt on desktop and in the top bar on phones. It shows what is left in whichever window is closest to its limit, per account.
- A Token usage pop-up under Settings, Account, with a progress bar and percentage for each of the daily, weekly and monthly limits.
- Stars and favourites for characters, from the home page, search and the chat sidebar. A Favourites filter on the home page.
- Character totals: stars and user-sent messages, shown to every account. The top 10% of characters with at least one message are labelled "popular". Group chats count once for each member character.
- Personas that each account creates for itself: a name, a colour, a free-text description and an optional picture. Only that account sees them.
- Profile pictures in the home page sidebar, with the colour face as the fallback.
- A back button at the top of the sidebar. On phones it closes the drawer, and on desktop it collapses the sidebar.
- Terminal client flag --no-image, which skips the character picture when a chat opens. /portrait still works.
- Account profile pages at /u/<username>, with a description, a profile picture, a background and the characters that account made. The creator handle on a character card opens that page; admin has no profile and its handle is not a link.
- Account settings now edit the profile and set whether other accounts may see the characters this account has liked. Liked characters are private until the account turns sharing on.

### Changed
- The desktop sidebar is 336px wide, up from 240px.
- On desktop, the requests counter sits in the same row as the token bar, under the prompt.
- The font, text size and Memory controls are only in Settings. They have left the desktop chat bar.
- The phone sidebar is the full screen width, with larger character pictures.

### Removed
- Unused styles for .bg-preview and .phone-only, and the leftover placeholder in the chat layout code.

## 1.3.1 - 2026-10-20

The first full release of the 1.3.1 line. It includes everything in 1.3.1-beta and the changes from its beta follow-ups, plus the clean-up below. It keeps the 1.3 protocol, so terminal clients from 1.3.0 still connect.

### Added

- Character backgrounds (up to 10 per character), random picks on chat open and timed cycling on cards.

- Face icons for characters with no picture, in the character's colour.

- Appearance sliders for background brightness and chat bubble opacity, kept per account.

- Settings as a grid of category cards.

- An optional private list of default word substitutions in `rp_core.py`. The published build ships the list empty; put your own in `substitutions.json`. Existing installs keep their own file.

### Changed

- Chat look and creativity sit under Appearance and Character. Persona is a card of its own.

- On phones the Undo button sits beside the Tree button.

- The terminal client's `CLIENT_VERSION` now matches the server version (1.3.1).

### Removed

- `safe_save.py`. It was a standalone script that nothing in the app imported. Memory banks are saved by the app's own writers, which were not changed.

- Unused internal helpers (`resolve_folder`, `caching_for`, `timestamp_now`, `_load_json_file`, and two `rp_core.py` methods that were never called). No route, response or screen changed.

### Upgrading

Follow `UPDATE.txt`. Keep `.env` safe: it holds `MISO_SECRET_KEY`, and two-factor secrets cannot be read without it.

## 1.3.1-beta-3 - 2026-10-20

A beta follow-up to 1.3.1-beta. It keeps the 1.3 protocol, so terminal clients from 1.3.0 still connect.

### Changed

- An optional private list of default word substitutions in `rp_core.py`; the published build ships it empty.

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

