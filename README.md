# Video Curator

Video Curator turns YouTube or Instagram videos into structured markdown notes for a local workflow review queue. It downloads a video, extracts transcript/frame context, summarizes transcript text through a local FreeLLMAPI-compatible router, asks Claude for a concise routing decision, and writes a note.

This public version contains no cookies, sessions, personal collection URLs, local machine paths, real `.env` values, or curated output.

## Prerequisites

- Python 3.11 or newer
- `ffmpeg` on `PATH`, or `FFMPEG_BIN` pointing to the directory containing it
- `yt-dlp`
- Claude CLI on `PATH` for the analysis stage
- Local FreeLLMAPI-compatible router, defaulting to `http://127.0.0.1:3001/v1`
- Optional for Instagram imports: Playwright browser install and a local Chrome cookie source

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configure

Copy `.env.example` into your own shell, launcher, or dotenv wrapper. The scripts read process environment variables directly.

Key settings:

- `FREELLMAPI_BASE_URL`: OpenAI-compatible chat-completions base URL. Default: `http://127.0.0.1:3001/v1`.
- `VIDEO_CURATOR_OUT_DIR`: note output directory. Default: `./curator-notes`.
- `VIDEO_CURATOR_LEARNINGS`: optional markdown file with standing curation rules. Default: `./learnings.md`.
- `FFMPEG_BIN`: optional directory containing `ffmpeg`.
- `IG_COLLECTION_URL`: optional Instagram saved collection URL for import/validation.
- `IG_COOKIE_FILE` or `IG_CHROME_PROFILE_DIR`: optional Instagram cookie source.
- `VIDEO_WATCH_COOKIES_FROM_BROWSER`: optional yt-dlp browser-cookie source, for example `chrome`.
- `GROQ_API_KEY` or `OPENAI_API_KEY`: optional Whisper fallback if a video has no captions.

## Run

Curate one video:

```bash
python curate.py "https://www.youtube.com/watch?v=VIDEO_ID" --out curator-notes --learnings learnings.md
```

Import from configured source lists:

```bash
python import_saved.py --sources import-sources.json --out curator-notes --state import-state.json --learnings learnings.md
```

Validate an Instagram saved collection without committing any session data:

```bash
python validate_ig_cookies.py --collection-url "https://www.instagram.com/<account>/saved/<collection>/"
```

Show CLI help:

```bash
python curate.py --help
```

## Source Config Format

`import_saved.py` expects JSON shaped like:

```json
{
  "youtube": ["https://www.youtube.com/playlist?list=..."],
  "instagram": {
    "collection_url": "https://www.instagram.com/<account>/saved/<collection>/"
  }
}
```

Keep real source files local. `import-sources*.json`, `import-state*.json`, cookies, browser profiles, `.env`, generated notes, and frames are ignored by git.

## Notes

Instagram access depends on your own account/session and may be subject to Instagram's terms and rate limits. Use this for personal review of content you can access, and do not ship cookies or session exports.

## Verify

```bash
python -m pytest -q
python curate.py --help
```
