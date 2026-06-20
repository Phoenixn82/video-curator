import subprocess, shutil, sys
def parse_ids(raw: str) -> list[str]:
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]
def _run_ytdlp_ids(playlist_url: str) -> list[str]:
    if shutil.which("yt-dlp") is None: raise SystemExit("yt-dlp not installed. pip install yt-dlp")
    r = subprocess.run(["yt-dlp","--flat-playlist","--print","%(id)s","--",playlist_url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[import] yt-dlp playlist read failed: {r.stderr.strip()}", file=sys.stderr); return []
    return parse_ids(r.stdout)
def youtube_items(playlist_url: str) -> list[tuple[str, str]]:
    return [(v, f"https://www.youtube.com/watch?v={v}") for v in _run_ytdlp_ids(playlist_url)]
