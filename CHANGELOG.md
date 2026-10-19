# Changelog

Notable changes to Miso Chat, newest first. Version numbers follow MAJOR.MINOR.PATCH. The current version is in `VERSION`.

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
- The built-in word substitutions start empty. Existing `substitutions.json` files are not touched.

## 1.0.0 - 2026-10-15

The first public version, merged from pull request #1. It had the web app and the terminal chat, with Gemini as the main backend and optional DeepSeek, local OpenAI-style server and Claude Code backends. It had per-account chat history, memory and persona choice, the admin page with usage caps, token budgets, providers, character cards and icons, and a stats page. It also added auto-fill and roll with a reworked phone chat header, word substitutions, manual peer sync on a local network, and wiki lookup from a character's `wiki.md`.

This version has no git tag. Its state is the merge commit of pull request #1.
