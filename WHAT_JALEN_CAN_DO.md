
# What Jalen can do  (161 things)

Generated from the code, not written by hand - so it cannot promise
something that no longer exists. Tick what works, note what doesn't.

`*` means you can say it out loud and it answers instantly, for free,
without asking Claude.

## Talking to Jalen
_Its own controls. All free, all instant, none need the network._

- [ ] * **acknowledge** - Acknowledge without acting
- [ ] * **ack** - Answer to its own name
- [ ] * **set_posture** - Change how cautious the safety gate is
- [ ] * **sleep** - Go quiet until you say the wake word
- [ ] * **timing** - How long the last turn actually took, broken down
- [ ] * **reload_config** - Re-read config without restarting
- [ ] * **restart** - Restart itself
- [ ] * **greet** - Say hello
- [ ] * **read_all** - Speak the rest of an answer that was cut short
- [ ] * **resume** - Start listening again
- [ ] * **quit** - Stop Jalen entirely
- [ ] * **pause** - Stop listening but stay running
- [ ] * **mute** - Stop talking, keep working
- [ ] * **cancel** - Stop what you are doing
- [ ] * **private_mode** - Stop writing anything to the audit log
- [ ] * **unmute** - Talk again
- [ ] * **morning_brief** - The day's summary on demand
- [ ] * **audit_digest** - What Jalen did today

## Checking on itself
_It can test and diagnose itself now._

- [ ]   **log_weakness** - Record a capability you DON'T have, after you've already answered him
- [ ] * **open_own_project** - Open Jalen's own source code in VS Code  [tells you first, you can say stop]
- [ ] * **own_health** - A two-sentence answer to 'are you alright' - disk, last shutdown, tools loaded
- [ ] * **review_weaknesses** - List what Jalen still can't do, most frequently hit first
- [ ] * **run_own_tests** - Run Jalen's OWN test suite and report what passed and failed
- [ ] * **self_diagnose** - Jalen's own diagnostics: packages, models, microphone, credentials, disk space, and what setup is still outstanding

## Handing work to other AIs
_Gemini, ChatGPT, Hermes, Claude Code, the desktop app._

- [ ] * **ask_claude_code** - Open Claude Code in a folder and start it working on a prompt  [tells you first, you can say stop]
- [ ] * **claude_code_status** - Check whether the Claude Code CLI is installed and where
- [ ]   **delegate_task** - Hand a task to another AI: gemini, or chatgpt (needs an API key)  [tells you first, you can say stop]
- [ ]   **follow_up_task** - Reply into an existing delegated conversation - the other AI keeps its own context, so name only what needs fixing rather than restating the whole ...  [tells you first, you can say stop]
- [ ]   **hand_off_to_code** - Start Claude Code on a fully written brief, in a project folder  [tells you first, you can say stop]
- [ ]   **hand_off_to_cowork** - Open the Claude desktop app with a fully written brief on the clipboard  [tells you first, you can say stop]
- [ ] * **list_coding_jobs** - What coding agents are running and what they finished
- [ ]   **list_delegations** - Conversations with other AIs, and how many rounds each has had
- [ ]   **master_prompt_guide** - The house standard for briefing another AI
- [ ]   **review_coding_job** - Read back a finished coding job so you can judge it
- [ ]   **review_delegation** - Read back a delegated conversation so you can judge it
- [ ]   **start_coding_job** - Start Claude Code on a real job in a folder, in the BACKGROUND  [tells you first, you can say stop]

## Driving ChatGPT and Gemini in a browser
_Opens Chrome, submits a work order, waits, reads the answer, and judges it against what you actually asked for._

- [ ] * **close_browser** - Close the browser Jalen was using for delegation
- [ ] * **list_web_chats** - The browser delegations so far, newest first
- [ ] * **open_signup** - Open the sign-up page for ChatGPT or Gemini and hand it to him
- [ ]   **read_web_result** - Re-read what ChatGPT or Gemini said in the browser, with the original objective and success criteria alongside it, so it can be judged
- [ ]   **web_delegate** - Hand a task to ChatGPT or Gemini in a REAL BROWSER and wait for the answer  [tells you first, you can say stop]
- [ ]   **web_follow_up** - Send a correction into the SAME browser conversation, and wait again  [tells you first, you can say stop]
- [ ] * **web_sign_in_state** - Whether he is signed in to ChatGPT or Gemini in Jalen's browser profile, and what to do about it

## Learning and feedback
_Getting faster at what you repeat, and asking how it did._

- [ ]   **forget_habit** - Forget a learned shortcut
- [ ]   **rating_history** - His recent ratings and the average, for 'how am I doing' or 'what have I scored'
- [ ]   **record_rating** - File a rating he gave for work just finished, and email a short summary to him
- [ ]   **what_i_have_learned** - The requests Jalen has learned to answer without asking the model, and how much time that has saved

## Code and projects
_Git, VS Code, and looking at what an agent changed._

- [ ]   **init_git_repo** - Start version control in a folder and commit a baseline  [tells you first, you can say stop]
- [ ]   **open_in_vscode** - Open a folder in VS Code  [tells you first, you can say stop]
- [ ]   **project_status** - Git branch, last commit and working-tree state of a project folder

## Email
_Reading, drafting and sending Gmail._

- [ ]   **draft_email** - Save a DRAFT in his Gmail
- [ ]   **draft_email_with_file** - Save a Gmail draft with a file attached
- [ ] * **google_status** - Report which Google account is connected, or what to run if none is
- [ ]   **read_email** - Read ONE message in full
- [ ]   **scan_inbox** - Go through a LOT of mail in one call — up to 300 — and sort it
- [ ]   **search_email** - Search his Gmail using Gmail's own query syntax — 'from:rodion', 'subject:visa', 'is:unread', 'after:2026/08/01'
- [ ]   **send_email** - SEND mail as him  [stops and waits for you to say yes]
- [ ] * **unread_email_headline** - Unread mail as ONE short spoken sentence — a count and the first few senders, no subjects and no message ids
- [ ] * **unread_email_summary** - List unread inbox mail, newest first, as sender + subject + snippet

## Telegram
_Your own chats and your channel._

- [ ]   **community_post_guide** - The layout rules for his AI Engineering & Machine Learning channel: bullet character, where bold goes, the expandable Q&A block, the fixed sign-off
- [ ] * **list_telegram_chats** - List his recent Telegram conversations with unread counts
- [ ] * **read_telegram** - Read recent messages from ONE Telegram chat, named the way he says it — 'Uluhbek', 'Saved Messages', an @username
- [ ]   **save_draft_text** - Write a long piece — an essay, a personal statement, an application answer, a post — to a file AND his clipboard
- [ ]   **save_telegram_draft** - Write text into a chat's DRAFT box without sending it
- [ ]   **search_telegram** - Search across all his Telegram messages for a phrase
- [ ]   **send_posts** - Send SEVERAL posts to one Telegram chat in a single call  [stops and waits for you to say yes]
- [ ]   **send_telegram_file** - Send a file to a Telegram chat  [stops and waits for you to say yes]
- [ ] * **send_telegram_message** - Send a Telegram message AS HIM to a person or group  [stops and waits for you to say yes]
- [ ] * **telegram_status** - Report which personal Telegram account is signed in, or what to run if none is
- [ ] * **telegram_unread** - The unread Telegram messages THEMSELVES, from the busiest chats — not a count

## Calendar

- [ ]   **calendar_status** - Report whether Calendar is connected, and today's events if it is
- [ ]   **create_calendar_event** - Add an event to his own calendar
- [ ] * **read_calendar** - List events on one day
- [ ]   **search_calendar** - Find upcoming events by text, from today forward

## The web

- [ ] * **close_browser_tab** - Close the browser window showing a page — 'close the YouTube window'  [tells you first, you can say stop]
- [ ]   **current_page_url** - The URL of the browser window he is looking at, read from Chrome's own address bar
- [ ] * **focus_browser_tab** - Bring the browser window showing a page to the front
- [ ] * **list_browser_tabs** - Every browser window and the page it is showing
- [ ] * **open_url** - Open a URL in the default browser
- [ ] * **play_on_youtube** - Search YouTube and start the first result playing
- [ ] * **search_site** - Search a specific website and show the results: YouTube, Google, GitHub, Reddit, Amazon, Wikipedia, Spotify, Maps and others
- [ ]   **web_read** - Fetch one web page and extract its readable text
- [ ]   **web_search** - Search the web and get the results back AS TEXT — titles, snippets and URLs you can actually read

## Files and folders

- [ ] * **clear_temp_files** - Delete the contents of the temp folders  [stops and waits for you to say yes]
- [ ] * **copy_file** - Copy a file to a destination path or folder
- [ ] * **create_file** - Create a new text file
- [ ] * **create_folder** - Create a folder, and any missing parent folders
- [ ]   **delete_file** - Delete a file  [stops and waits for you to say yes]
- [ ]   **edit_file** - Overwrite an existing file's contents  [tells you first, you can say stop]
- [ ]   **list_aliases** - List every nickname taught so far
- [ ]   **list_directory** - List a folder's contents
- [ ]   **move_file** - Move a file to a destination path or folder
- [ ] * **open_folder** - Open a folder in File Explorer
- [ ] * **open_in** - Launch an application already pointed at a file or folder, e.g
- [ ] * **open_target** - Open exactly ONE thing by name: an application, a file, or a folder
- [ ] * **read_document** - Extract readable text from a document: plain text, markdown, csv, json, code/config files, .docx/.pptx/.xlsx (read directly as the zip-of-XML they ...
- [ ]   **read_file** - Read a text file's contents
- [ ]   **remember_alias** - Teach a nickname for an app, file or folder so it can be opened by that name later
- [ ] * **rename_file** - Rename a file in place
- [ ] * **search_files** - Find files by (partial) name
- [ ] * **search_in_files** - Find files whose CONTENTS contain a phrase, not just the filename
- [ ]   **summarize_document** - Extract a document's text, clearly labelled with its name/path/type, so it can be summarised
- [ ] * **temp_file_report** - How much space the temp folders are using

## This machine
_Windows, windows, volume, media, screenshots._

- [ ]   **click_element** - Click a labelled control inside a window
- [ ] * **close_app** - Close a running app's window by (partial) title
- [ ] * **disk_report** - Report free space per drive, plus the biggest folders and files in the user's Desktop, Documents, Downloads and Temp folders
- [ ] * **empty_recycle_bin** - Permanently empty the Recycle Bin  [stops and waits for you to say yes]
- [ ] * **focus_window** - Bring a window to the foreground by (partial) title
- [ ]   **forget_site_decision** - Undo a standing decision, so the site is asked about again  [stops and waits for you to say yes]
- [ ] * **get_battery** - Battery level, if this machine has one
- [ ] * **get_date** - Current date
- [ ] * **get_system_status** - CPU, memory and disk usage
- [ ] * **get_time** - Current time
- [ ] * **get_window_list** - List currently visible top-level windows
- [ ] * **keyboard_shortcut** - Send a keyboard shortcut
- [ ]   **lock_vault** - Forget the unlocked secrets immediately
- [ ] * **lock_workstation** - Lock the screen
- [ ] * **media_next** - Skip to the next track
- [ ] * **media_play_pause** - Play or pause whatever media player currently has focus
- [ ] * **media_previous** - Go back to the previous track
- [ ]   **memory_report** - Report overall RAM usage and the top processes by memory use
- [ ] * **open_app** - Launch an application by name
- [ ] * **open_windows_security** - Show Windows Security / Defender
- [ ] * **open_windows_update** - Show the Windows Update page
- [ ]   **read_screen** - Read a window's UI as text via the accessibility tree — far cheaper and more reliable than a screenshot for reading what's on screen
- [ ]   **refresh_system_scan** - Force a fresh disk/cleanup scan in the background
- [ ] * **screenshot** - Take a screenshot and save it to disk
- [ ] * **sign_out** - Log the current Windows user out  [stops and waits for you to say yes]
- [ ]   **type_text** - Type text into whatever control is focused in a window
- [ ]   **unlock_vault** - Unlock the vault with a passphrase you already have as text  [stops and waits for you to say yes]
- [ ]   **unlock_vault_prompt** - Unlock the vault  [tells you first, you can say stop]
- [ ] * **volume_mute_toggle** - Mute or unmute system audio output
- [ ] * **volume_set** - Set system volume to an absolute percentage
- [ ] * **volume_step** - Nudge system volume up or down a few steps
- [ ] * **window_state** - Minimise or maximise the current foreground window

## The technician
_Diagnosing and repairing the machine._

- [ ] * **cleanup_suggestions** - Report specifically safe-to-delete things with real measured sizes: Temp files, old Downloads, recycle bin, browser/pip/npm caches
- [ ] * **diagnose** - Find out what is actually wrong with this machine
- [ ] * **diagnose_wifi** - Why the wifi keeps dropping: adapter power saving, signal strength, and how many times it actually disconnected in the last two days
- [ ] * **fix_wifi_power_saving** - Stop Windows powering the wireless adapter down to save battery — the commonest cause of wifi dropping every few minutes on a laptop  [tells you first, you can say stop]
- [ ] * **open_startup_settings** - Show which programs start with Windows

## Passwords and logins
_The vault, and typing into forms._

- [ ]   **fill_credential** - Type a stored secret into the field HE HAS FOCUSED  [tells you first, you can say stop]
- [ ]   **fill_field** - Type ordinary text into the focused field — a name, a phone number, a short answer
- [ ]   **fill_form_field** - Type a value into one named field  [tells you first, you can say stop]
- [ ]   **fill_login_field** - Type his saved password into this page's password box  [stops and waits for you to say yes]
- [ ]   **list_secrets** - The NAMES of what is stored — never the values
- [ ]   **list_site_decisions** - Every site he has approved or blocked, so he can audit it
- [ ]   **remember_site_decision** - Record that he approves a site FOREVER, or blocks it forever  [stops and waits for you to say yes]
- [ ]   **site_permission** - What he has already decided about typing his details into a site: always, never, or ask
- [ ]   **vault_status** - Whether the credentials vault exists and is unlocked

## Remembering

- [ ]   **recall_memory** - Search remembered facts and preferences by meaning, not exact wording
- [ ]   **remember** - Store a fact or preference for later recall

## Writing as you

- [ ]   **list_drafts** - The drafts written so far, newest first
- [ ]   **voice_guide** - Return HIS writing-voice guide

## Asking you things

- [ ]   **ask_user** - Ask him something and WAIT for the answer

## Everything else

- [ ]   **form_errors** - What the form is complaining about right now
- [ ]   **inspect_form** - List every field on the form currently open in Jalen's browser: its label, type, whether it is required, and whether it is already filled
- [ ]   **next_field** - Press Tab to move to the next field
- [ ]   **open_storage_settings** - Show where the disk space went
- [ ] * **play_media** - Play something he named without saying where: a local media file if one matches, YouTube otherwise
- [ ]   **submit_form** - Submit the form and report what the page said back, including any validation errors  [stops and waits for you to say yes]
- [ ]   **upload_to_form** - Attach a real file to the form's file input - a CV, a PDF, an image  [tells you first, you can say stop]

---

143 tools: 18 amber, 112 green, 13 red
86 of them also answer to a spoken phrase, for free.

Not in this list, because they are not tools:
  - the orb: size it by voice or Ctrl+Alt+B / Ctrl+Alt+S
  - the wake word, barge-in, and the follow-up window
  - the safety gate itself

