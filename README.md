# sandbox

A content lab you run by talking to Claude. Bring your own footage and photos (they all go in one folder, `library/_external content/`), add a Pinterest board, or let Claude grab clips for you from TikTok, YouTube, Instagram, X, Reddit and most other sites. Claude judges what's good and makes finished short videos and photo slideshows for the accounts you choose: a suggested caption for each (or put on in TikTok's own font when you ask), cut to the beat of a song you give it or the sound from any TikTok, lyrics appearing word by word. You review everything it makes; anything you mark **Perfect** lands in that account's exports folder, ready to post, and your notes teach it your taste. If you like to finish things yourself in DaVinci Resolve, it hands you the rough cut as a Resolve timeline: it curates, you refine.

**sandbox is in beta.** If Claude gets something wrong or not quite how you like it, just say so and it adjusts.

john built this shit and hes so sexy and humble

## Set up: one step
On a Mac (a brand-new one is fine), open **Terminal** (press ⌘ Space, type `Terminal`, press Return), paste this line (⌘ V) and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/kalaradente/sandbox_public/main/install.sh | bash
```

It installs anything missing (Apple's command line tools, Homebrew, ffmpeg, deno for YouTube, Python and its packages), puts the sandbox in `~/Desktop/sandbox`, and does a test render. It needs an administrator account and about 6 GB free, and the first run takes 15-30 minutes, mostly downloads. Along the way:
- **It asks for your Mac password.** Type it and press Return. Nothing shows while you type; that's normal.
- **A window may ask to install Apple's "command line developer tools".** Click Install and let it finish; the setup carries on by itself.
- When it says **Done**, you can close Terminal. You won't need it again: from here on you just talk to Claude.

Then open the **Claude desktop app → Code tab**, choose the `sandbox` folder on your Desktop, and say hi. (No Claude app yet? Get it at [claude.ai/download](https://claude.ai/download). Claude Code needs a paid Claude plan.) The first time, Claude checks your setup, asks which accounts you'd like videos made for and what you want from it, and shows you around.

## Talking to Claude here
| Say | What happens |
|---|---|
| "make me …" | a batch for what you asked ("5 nostalgic ones for my main account", "a slideshow from my trip photos") |
| (drop a song in the chat) | "here's my song": Claude sets it up to cut edits to (add the lyrics too if you want them on screen) and, if you use Resolve, gives you a script to mark the moments edits should start on: run it, add markers, **run it again to save them**, then tell Claude (in a new Resolve project, its "Copy markers to every timeline" puts them on your timelines) |
| "use the sound from …" | takes the audio from a TikTok (one it downloaded, or a link) to cut an edit to |
| "put it in Resolve" | turns a piece into a DaVinci Resolve timeline you can refine (use Resolve 21.0.4 or any paid version: a newer free Resolve breaks the scripts; setup tells you, and Claude has the download link) |
| "check" | collects what you saved as Gmail drafts (Share → Mail → the X in the top left → Save Draft, from pretty much anywhere: TikTok, YouTube and Shorts, Instagram, Facebook, X, Reddit, Snapchat Spotlight, Tumblr, Twitch clips, Bluesky, Vimeo, Dailymotion, Streamable, Imgur, LinkedIn, Pinterest, and most other public video pages), downloads clean copies and indexes them. Or just paste links into the chat |
| "index my content" | gets your own footage ready to cut from. It lives in `library/_external content/`, your main content folder: drop in your clips, photos, whole shoots (subfolders are fine). Claude tells you what indexing each folder would cost and does the one you mean; a big dump is taken a part at a time. "open my content folder" opens it |
| "pull my Pinterest board" | brings in the pins from your board for slideshows |
| "review" | opens the review page |
| "study …" | finds out what works and why ("study my saved videos", "study my posts") |
| "show me my exports" | opens the folder with your finished videos (AirDrop them to your phone to post) |
| "update" (or "upgrade") | gets the latest version of sandbox |
| "how does this work?" | the tour again |

## Reviewing (this is the training)
The review page plays each piece: hit **Keep**, **Perfect** or **Delete** (K / P / D), add a short note on why, and tap what it's about if you like (1-7: opener, caption, pictures, music match, feel, idea, quality). Perfect copies it into that account's exports folder. Nothing is ever erased, and every note teaches the next batch.

## Where things live
- `library/`: your footage, photos, songs, batches, exports and settings. Git never touches it.
- Everything else is the engine. Updates arrive with `git pull && ./setup.sh`.
