#!/usr/bin/env python3
"""video_curator/curate.py — lean video curation pipeline.

CLI contract (exact):
    python "C:\\...\\video_curator\\curate.py" "<URL>" \
        --out "C:\\...\\briefing\\curator" \
        --learnings "C:\\...\\video-curator\\learnings.md" \
        [--force]

Idempotent: if a note already exists in --out for this video, the pipeline is skipped (no
token spend) unless --force is passed. Delete a note to re-curate that video.

Stages:
  1. SCRAPE  (0 LLM tokens) — vendor/watch.py (yt-dlp + ffmpeg)
  2. INGEST  (local router) — FreeLLMAPI summarizes transcript text
  3. CLAUDE  (scarce/cached) — minimal structured judgment
  4. WRITE   (0 LLM tokens) — writes <yt|ig>-<id>.md to --out
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import urllib.error
from urllib.parse import urlparse
import urllib.request

# ---------------------------------------------------------------------------
# Optional ffmpeg PATH augmentation
# ---------------------------------------------------------------------------
FFMPEG_BIN = os.environ.get("FFMPEG_BIN") or os.environ.get("VIDEO_CURATOR_FFMPEG_BIN")
if FFMPEG_BIN and Path(FFMPEG_BIN).expanduser().exists():
    os.environ["PATH"] = str(Path(FFMPEG_BIN).expanduser()) + os.pathsep + os.environ.get("PATH", "")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent.resolve()
VENDOR_DIR = PROJECT_DIR / "vendor"

# Skills snapshot: name -> one-line purpose (static, cached by Claude)
# Extend this list as skills change; keep it minimal — one line each.
SKILLS_SNAPSHOT = """\
video-watch: Download video via yt-dlp, extract frames + transcript (default scraper)
video-lens: Publish durable HTML video report to ~/Downloads/video-lens/reports/
chrome-devtools-mcp: Drive Chrome DevTools from Claude/Codex for browser automation
claude-codex-browser: Wire Claude + Codex to share a live browser session (bidirectional)
obsidian-ingest: Ingest any source into the Obsidian vault; rewrites stale claims
obsidian-save: Save conversation outputs to the vault
obsidian-task: Add a task to the right kanban board with inferred priority
research: Web research with citations via Perplexity Sonar
research-deep: Vault-first deep research; fills gaps via Perplexity + Grok
freellmapi: Local free-LLM router — task-aware model presets (code/agent/fast/long/reason)
codex: Codex CLI dispatch — all code-writing and mechanical tasks
superpowers:brainstorming: Explore intent + design before any implementation
superpowers:writing-plans: Multi-step plan from a spec, before touching code
superpowers:executing-plans: Execute written plans with review checkpoints
superpowers:subagent-driven-development: Parallel independent tasks in one session
superpowers:systematic-debugging: Structured bug diagnosis before proposing fixes
superpowers:test-driven-development: Tests first, then implementation
superpowers:verification-before-completion: Verify before claiming work is done
update-config: Configure Claude Code harness via settings.json
vercel:nextjs: Next.js App Router expert guidance
vercel:deploy: Deploy current project to Vercel
vercel:ai-sdk: Vercel AI SDK — chat, streaming, tool use, agents, MCP
"""

# ---------------------------------------------------------------------------
# Stage 1: SCRAPE via vendor/watch.py
# ---------------------------------------------------------------------------

def _augment_watch_path() -> dict[str, str]:
    """Return env with SCRIPT_DIR on sys.path so vendor imports resolve."""
    env = os.environ.copy()
    # watch.py inserts SCRIPT_DIR into sys.path; vendor/ is that dir
    return env


def scrape(url: str, work_dir: Path) -> dict:
    """Run vendor/watch.py on <url>, capture stdout (markdown report) + frames dir.

    Returns:
        {
          "work_dir": Path,
          "frames_dir": Path,
          "frames": [{"path": str, "timestamp_seconds": float}, ...],
          "transcript": str,          # formatted transcript text or ""
          "report_md": str,           # full stdout from watch.py
          "video_info": {"title": ..., "uploader": ..., "url": ...},
        }
    """
    watch_py = VENDOR_DIR / "watch.py"
    if not watch_py.exists():
        raise RuntimeError(f"vendor/watch.py not found at {watch_py}")

    out_dir = work_dir / "watch"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        str(watch_py),
        url,
        "--frames-per-hour", "30",
        "--out-dir", str(out_dir),
    ]

    env = os.environ.copy()
    # Ensure vendor/ is on PYTHONPATH so watch.py's local imports work
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(VENDOR_DIR) + (os.pathsep + existing_pp if existing_pp else "")

    print(f"[curate] stage 1: scrape {url}", file=sys.stderr)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=600,
    )
    # watch.py prints its working dir to stderr and report to stdout.
    # Non-zero exit is only fatal if no frames were extracted.
    report_md = result.stdout or ""

    frames_dir = out_dir / "frames"
    frames: list[dict] = []
    if frames_dir.exists():
        for p in sorted(frames_dir.glob("frame_*.jpg")):
            frames.append({"path": str(p)})

    # Parse transcript from report_md (block between ```...```)
    transcript = ""
    m = re.search(r"## Transcript\n\n.*?```\n(.*?)```", report_md, re.DOTALL)
    if m:
        transcript = m.group(1).strip()

    # Parse video info from report_md header bullets
    video_info: dict = {"url": url}
    title_m = re.search(r"\*\*Title:\*\*\s+(.+)", report_md)
    if title_m:
        video_info["title"] = title_m.group(1).strip()
    uploader_m = re.search(r"\*\*Uploader:\*\*\s+(.+)", report_md)
    if uploader_m:
        video_info["uploader"] = uploader_m.group(1).strip()

    if result.returncode != 0 and not frames:
        raise RuntimeError(
            f"watch.py failed (exit {result.returncode}):\n{result.stderr[-1000:]}"
        )

    print(f"[curate] scraped {len(frames)} frames", file=sys.stderr)
    return {
        "work_dir": out_dir,
        "frames_dir": frames_dir,
        "frames": frames,
        "transcript": transcript,
        "report_md": report_md,
        "video_info": video_info,
    }


# ---------------------------------------------------------------------------
# Stage 2: GEMINI INGEST (cheap, objective description)
# ---------------------------------------------------------------------------

_GEMINI_INGEST_SYSTEM = (
    "You read a video's transcript and produce an objective, detailed account "
    "of what the video is about — its subject, what it teaches or demonstrates, "
    "and the tools, commands, URLs, products, and steps named in it. Describe "
    "the VIDEO ONLY. Never mention the transcript, the request, or your own "
    "process; never write 'the user provided', 'I have', or 'the video opens "
    "with a frame'. Output plain prose."
)


def freellmapi_urls() -> tuple[str, str, str]:
    base = os.environ.get("FREELLMAPI_BASE_URL", "http://127.0.0.1:3001/v1").rstrip("/")
    if base.endswith("/v1"):
        api_base = base
        root = base[:-3].rstrip("/")
    else:
        root = base
        api_base = f"{base}/v1"
    return f"{root}/api/settings/api-key", f"{api_base}/chat/completions", api_base


def gemini_ingest(frames: list[dict], transcript: str, prompt_file: Path) -> str:
    """Read the transcript through the local FreeLLMAPI router."""
    transcript = (transcript or "").strip()
    if not transcript or transcript == "(no transcript available)":
        raise RuntimeError("gemini_ingest: no transcript to summarize")

    key_url, chat_url, display_base = freellmapi_urls()

    def read_json(req: urllib.request.Request, timeout: int) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"gemini_ingest: FreeLLMAPI returned HTTP {exc.code} for {exc.url}: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"gemini_ingest: FreeLLMAPI router unreachable at {display_base}: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"gemini_ingest: FreeLLMAPI returned invalid JSON from {req.full_url}") from exc

    key_resp = read_json(urllib.request.Request(key_url, method="GET"), timeout=30)
    api_key = (key_resp.get("apiKey") or "").strip()
    if not api_key:
        raise RuntimeError("gemini_ingest: FreeLLMAPI did not return an apiKey")

    payload = {
        "model": "gemini-2.5-flash",
        "max_tokens": 1200,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _GEMINI_INGEST_SYSTEM},
            {"role": "user", "content": "Transcript:\n\n" + transcript[:12000]},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        chat_url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    print("[curate] stage 2: read transcript via FreeLLMAPI", file=sys.stderr)
    resp = read_json(req, timeout=120)
    choices = resp.get("choices") or []
    content = ""
    if choices:
        content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("gemini_ingest: FreeLLMAPI returned empty content")
    return content


# ---------------------------------------------------------------------------
# Stage 3: CLAUDE ANALYSIS (scarce — minimal + cached)
# ---------------------------------------------------------------------------

_VALID_ROUTES = (
    "improvements_jsonl",
    "operational_fix",
    "stack_edit",
    "edit_existing_skill",
    "obsidian_vault",
    "no-op",
)

_SCORE_RE = re.compile(r"relevance\s*score[:\s]*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_BARE_SCORE_RE = re.compile(r"\b(0\.[0-9]+)\b")

_CLAUDE_SYSTEM = """\
You are the analysis stage of a lean video-curation pipeline for Phoenix (an
AI-first power user whose stack is: Claude + Codex + ~130 skills + Obsidian
vault + Vercel/Next.js projects).

You receive:
 A. Gemini's objective account of the video (what was shown + said)
 B. A distilled snapshot of Phoenix's installed skills (name + one-line purpose)
 C. Phoenix's standing curation rules (learnings.md)

Your job: produce EXACTLY 5 sections in this order, each headed with ##:

## What this video is about
2-4 plain-language sentences on the video's actual SUBJECT and content — what it
teaches or demonstrates — for a human skimming a card. Describe the VIDEO ONLY.
Never describe the frames, the transcript, the request, or your own process; never
write "the user provided", "I have…", or "the video opens with a frame".

## Descriptive technique title
One line. Describes the transferable technique — NOT the clickbait caption.
Example: "claude + codex browser workflow"
Apply learnings rule 2.

## Actionable extract
3-5 bullets. Face-value transferable technique. Concrete and buildable.
Apply learnings rules 1, 3, 5.
Do NOT build ideologies around the demo example.
Do NOT propose mobile/data-science work outside Phoenix's stack.

## Routing decision
One label on its own line. Choose exactly one of:
  improvements_jsonl   — a concrete technique Phoenix can add to his workflow this week
  operational_fix      — a fix to an existing process or config
  stack_edit           — a change to the installed skill set (add/remove/edit skill)
  edit_existing_skill  — edit body of an existing skill (minor update)
  obsidian_vault       — reference material worth saving, no immediate action
  no-op                — not relevant to Phoenix's workflow

## Confidence + cost notes
Include "relevance score: 0.NN" (float 0.0–1.0). One or two lines max.
Apply learnings rule 5: score relative to Phoenix's real stack, not generic applicability.

---
Standing curation rules (learnings.md):

{learnings}

---
Phoenix's installed skills (name → one-line purpose):

{skills_snapshot}

---
Gemini's objective account of the video:

{gemini_account}
"""


def _parse_claude_output(text: str) -> dict:
    """Extract title, extract, route, score from Claude's 4-section output."""
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.*)$", text, re.MULTILINE))
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[heading] = text[start:end].strip()

    def find_section(*keywords: str) -> str:
        for k, v in sections.items():
            if any(kw in k for kw in keywords):
                return v
        return ""

    summary = find_section("this video is about", "what this video", "about the video")
    title = find_section("title", "technique")
    extract = find_section("actionable", "extract")
    routing_body = find_section("routing")
    confidence_text = find_section("confidence", "cost")

    # Pick first valid route token found
    routing_lower = routing_body.lower()
    route = "no-op"
    for r in _VALID_ROUTES:
        if re.search(r"\b" + re.escape(r) + r"\b", routing_lower):
            route = r
            break

    # Score
    score_match = _SCORE_RE.search(text)
    if score_match:
        score = float(score_match.group(1))
    else:
        bare = _BARE_SCORE_RE.search(confidence_text)
        score = float(bare.group(1)) if bare else 0.5

    return {
        "summary": summary.strip(),
        "title": title.strip(),
        "extract": extract.strip(),
        "route": route,
        "score": score,
        "raw": text,
    }


def claude_analysis(
    gemini_account: str,
    learnings_md: str,
    prompt_file: Path,
) -> dict:
    """Call claude CLI with a minimal cached prompt. Return parsed dict."""
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI not found on PATH")

    prompt_text = _CLAUDE_SYSTEM.format(
        learnings=learnings_md.strip(),
        skills_snapshot=SKILLS_SNAPSHOT.strip(),
        gemini_account=gemini_account.strip(),
    )
    prompt_file.write_text(prompt_text, encoding="utf-8")

    # claude --print reads stdin, outputs to stdout, no interactive session
    cmd = [exe, "--print", "--model", "claude-sonnet-4-6"]
    print("[curate] stage 3: claude analysis", file=sys.stderr)
    with open(prompt_file, "r", encoding="utf-8") as f:
        result = subprocess.run(
            cmd,
            stdin=f,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[-500:]}")
    return _parse_claude_output(result.stdout)


# ---------------------------------------------------------------------------
# Video ID extraction
# ---------------------------------------------------------------------------

def extract_id(url: str) -> tuple[str, str]:
    """Return (platform, id) from a YouTube or Instagram URL.

    platform: "yt" or "ig"
    id: the video/reel/post identifier
    Falls back to a slug of the URL if unrecognised.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    if "youtube.com" in netloc or "youtu.be" in netloc:
        # youtu.be/<id>
        if "youtu.be" in netloc:
            return "yt", parsed.path.lstrip("/").split("/")[0]
        # youtube.com/watch?v=<id>
        qs = {}
        for part in parsed.query.split("&"):
            if "=" in part:
                k, _, v = part.partition("=")
                qs[k] = v
        if "v" in qs:
            return "yt", qs["v"]
        # youtube.com/shorts/<id>
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return "yt", parts[1]
        return "yt", parts[-1] if parts else "unknown"

    if "instagram.com" in netloc:
        # instagram.com/p/<id>/ or /reel/<id>/
        parts = parsed.path.strip("/").split("/")
        # find segment after p, reel, tv
        for marker in ("p", "reel", "tv"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return "ig", parts[idx + 1]
        return "ig", parts[-1] if parts else "unknown"

    # Fallback: slugify netloc+path
    slug = re.sub(r"[^a-zA-Z0-9]", "-", (netloc + parsed.path).strip("/"))[:40]
    return "vid", slug or "unknown"


# ---------------------------------------------------------------------------
# Stage 4: WRITE NOTE
# ---------------------------------------------------------------------------

def write_note(
    out_dir: Path,
    platform: str,
    video_id: str,
    url: str,
    video_info: dict,
    gemini_account: str,
    transcript: str,
    claude_result: dict,
    frames: list[dict],
) -> Path:
    """Write the curator note to out_dir/<platform>-<id>.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    note_path = out_dir / f"{platform}-{video_id}.md"

    captured = datetime.now(timezone.utc).isoformat()
    title = claude_result.get("title") or video_info.get("title") or f"{platform}:{video_id}"

    # Persist a representative frame OUT of the (auto-deleted) temp dir, next to the note,
    # so the app can render it. Middle frame is less likely to be a title card / black frame.
    thumbnail = ""
    if frames:
        try:
            frames_dir = out_dir / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            src = frames[len(frames) // 2]["path"]
            dst = frames_dir / f"{platform}-{video_id}.jpg"
            shutil.copy2(src, dst)
            thumbnail = str(dst)
        except Exception:
            thumbnail = ""

    # Human-facing summary comes from Claude's synthesis (stage 3), NOT raw Gemini output —
    # Gemini sometimes narrates its own process or the frames ("the user provided…") instead of
    # the video. Fall back to the first 3 sentences of the Gemini account only if Claude omitted it.
    understand_body = (claude_result.get("summary") or "").strip()
    if not understand_body:
        # Claude omitted the summary section (rare). Do NOT fall back to raw Gemini output —
        # that is the original source of the process-narration leak ("the user provided…").
        # Use already-clean fields instead: the real video title, then Claude's technique title.
        understand_body = (video_info.get("title") or claude_result.get("title") or "").strip()
    summary = " ".join(understand_body.split())  # one-line form for frontmatter

    # Cap the transcript stored in the note. A card shows the summary + extract; the transcript
    # is collapsible reference only. A multi-hour video's full transcript bloated a single note
    # to ~700 KB, which is unusable in the detail overlay — keep a generous preview instead.
    TRANSCRIPT_CAP = 8000
    transcript_note = transcript or "(no transcript available)"
    if len(transcript_note) > TRANSCRIPT_CAP:
        transcript_note = transcript_note[:TRANSCRIPT_CAP].rstrip() + "\n\n…(transcript truncated)"

    fm_lines = [
        "---",
        "source: video-curator",
        f"source_url: {url}",
        f"captured: {captured}",
        f"title: {title}",
        f"summary: {summary}",
        f"thumbnail: {thumbnail}",
        "---",
        "",
        f"# {platform}:{video_id}",
        "",
        "## Understand the video",
        "",
        understand_body,
        "",
        "## Transcript",
        "",
        transcript_note,
        "",
        "## Actionable extract",
        "",
        claude_result.get("extract") or "(none)",
        "",
        "## Routing decision",
        "",
        claude_result.get("route", "no-op"),
        "",
        "## Confidence + cost notes",
        "",
        f"relevance score: {claude_result.get('score', 0.5):.2f}",
        "",
    ]

    note_path.write_text("\n".join(fm_lines), encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    url: str,
    out_dir: Path,
    learnings_path: Path,
    force: bool = False,
    from_existing: bool = False,
) -> int:
    platform, video_id = extract_id(url)
    print(f"[curate] identified: {platform}:{video_id}", file=sys.stderr)

    # Idempotency: a note already in the output dir means this video has been curated — skip the
    # whole (token-spending) scrape -> gemini -> claude pipeline unless --force. The note file IS
    # the record of "what's been curated", so deleting a note is how you ask for a re-curation.
    note_path = out_dir / f"{platform}-{video_id}.md"
    if from_existing and not note_path.exists():
        raise RuntimeError(f"--from-existing requested but note not found: {note_path}")
    if note_path.exists() and not (force or from_existing):
        print(
            f"[curate] already curated: {note_path.name} — skipping (pass --force to re-curate)",
            file=sys.stderr,
        )
        print(str(note_path))
        return 0

    learnings_md = ""
    if learnings_path.exists():
        learnings_md = learnings_path.read_text(encoding="utf-8")
    else:
        print(f"[curate] warning: learnings file not found at {learnings_path}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="curate-") as tmpdir:
        tmp = Path(tmpdir)

        if from_existing:
            print(f"[curate] stage 1: read existing note {note_path.name}", file=sys.stderr)
            note_md = note_path.read_text(encoding="utf-8")
            m = re.search(
                r"^## Transcript\s*\n(.*?)(?=^## Actionable extract\s*$)",
                note_md,
                re.DOTALL | re.MULTILINE,
            )
            if not m:
                raise RuntimeError(f"--from-existing could not find ## Transcript in {note_path}")
            transcript = m.group(1).strip()
            marker = "…(transcript truncated)"
            if transcript.endswith(marker):
                transcript = transcript[: -len(marker)].rstrip()

            video_info = {"url": url}
            fm = re.match(r"^---\n(.*?)\n---\n", note_md, re.DOTALL)
            if fm:
                for line in fm.group(1).splitlines():
                    key, _, value = line.partition(":")
                    if key == "title" and value.strip():
                        video_info["title"] = value.strip()

            frames = []
            existing_frame = out_dir / "frames" / f"{platform}-{video_id}.jpg"
            if existing_frame.exists():
                frame_copy = tmp / existing_frame.name
                shutil.copy2(existing_frame, frame_copy)
                frames = [{"path": str(frame_copy)}]

            scrape_result = {
                "frames": frames,
                "transcript": transcript,
                "video_info": video_info,
            }
        else:
            # Stage 1: scrape
            scrape_result = scrape(url, tmp)

        transcript = (scrape_result["transcript"] or "").strip()
        if not transcript or transcript == "(no transcript available)":
            # No transcript (e.g. a music video or a reel with no captions) — there is no
            # content to summarize, and FreeLLMAPI vision is unreliable. Write a clean,
            # title-only note instead of failing or leaving stale process-narration. Score 0
            # so it sorts to the bottom of the deck.
            print("[curate] no transcript — writing clean title-only note", file=sys.stderr)
            vtitle = scrape_result["video_info"].get("title") or f"{platform}:{video_id}"
            gemini_account = ""
            claude_result = {
                "summary": vtitle,
                "title": vtitle,
                "extract": "(no transcript available — not analyzed)",
                "route": "no-op",
                "score": 0.0,
                "raw": "",
            }
        else:
            # Stage 2: ingest transcript via FreeLLMAPI
            gemini_prompt = tmp / "gemini_prompt.txt"
            gemini_account = gemini_ingest(scrape_result["frames"], transcript, gemini_prompt)

            # Stage 3: claude analysis
            claude_prompt = tmp / "claude_prompt.txt"
            claude_result = claude_analysis(gemini_account, learnings_md, claude_prompt)

        # Stage 4: write note
        note_path = write_note(
            out_dir=out_dir,
            platform=platform,
            video_id=video_id,
            url=url,
            video_info=scrape_result["video_info"],
            gemini_account=gemini_account,
            transcript=scrape_result["transcript"],
            claude_result=claude_result,
            frames=scrape_result["frames"],
        )

    print(f"[curate] done — note written to {note_path}", file=sys.stderr)
    print(str(note_path))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="curate",
        description=(
            "Token-frugal video curation pipeline. "
            "Scrapes a video (yt-dlp + ffmpeg), runs Gemini ingest, "
            "then Claude analysis, and writes a structured curator note."
        ),
    )
    ap.add_argument(
        "url",
        nargs="?",
        help="YouTube or Instagram video URL to curate",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("VIDEO_CURATOR_OUT_DIR", "curator-notes")),
        help="Output directory for curator notes (default: ./curator-notes or VIDEO_CURATOR_OUT_DIR)",
    )
    ap.add_argument(
        "--learnings",
        type=Path,
        default=Path(os.environ.get("VIDEO_CURATOR_LEARNINGS", "learnings.md")),
        help="Path to learnings.md (default: ./learnings.md or VIDEO_CURATOR_LEARNINGS)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-curate even if a note already exists for this video (default: skip already-curated)",
    )
    ap.add_argument(
        "--from-existing",
        action="store_true",
        help="re-run analysis from the existing note transcript without scraping",
    )
    args = ap.parse_args()

    if args.url is None:
        ap.error("url is required")

    return run(
        url=args.url,
        out_dir=args.out,
        learnings_path=args.learnings,
        force=args.force,
        from_existing=args.from_existing,
    )


if __name__ == "__main__":
    raise SystemExit(main())
