import json, re
from pathlib import Path
_NOTE_RE = re.compile(r"^(?:yt|ig)-(.+)\.md$", re.IGNORECASE)

def _load(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("youtube_seen", []); d.setdefault("instagram_seen", []); return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"youtube_seen": [], "instagram_seen": []}

def seen_ids(state_path: Path, out_dir: Path) -> set[str]:
    d = _load(state_path)
    seen = set(d["youtube_seen"]) | set(d["instagram_seen"])
    try:
        for f in out_dir.iterdir():
            m = _NOTE_RE.match(f.name)
            if m: seen.add(m.group(1))
    except OSError:
        pass
    return seen

def unseen(ids: list[str], seen: set[str]) -> list[str]:
    out, local = [], set()
    for i in ids:
        if i not in seen and i not in local: out.append(i); local.add(i)
    return out

def mark_seen(state_path: Path, source: str, vid: str) -> None:
    key = "youtube_seen" if source == "youtube" else "instagram_seen"
    d = _load(state_path)
    if vid not in d[key]: d[key].append(vid)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(d, indent=2), encoding="utf-8")
