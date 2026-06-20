import argparse, json, subprocess, sys
from pathlib import Path
import enumerate_sources as en
import import_state as st
CURATE = Path(__file__).parent / "curate.py"

def _log(m: str) -> None: print(f"[import] {m}", flush=True)

def _curate_one(url: str, out_dir: Path, learnings: Path) -> int:
    return subprocess.run([sys.executable, str(CURATE), url, "--out", str(out_dir),
                           "--learnings", str(learnings)]).returncode

def _load_sources(p: Path) -> dict:
    try: return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log(f"could not read sources {p}: {e}"); return {"youtube": [], "instagram": {}}

def _instagram_candidates(ig_cfg: dict) -> list[tuple[str, str, str]]:
    url = ig_cfg.get("collection_url")
    if not url: return []
    import ig_collection as ig
    items = [("instagram", sc, f"https://www.instagram.com/p/{sc}/") for sc in ig.collection_shortcodes(url)]
    _log(f"instagram collection → {len(items)} candidates")
    return items

def run(sources_path: Path, out_dir: Path, state_path: Path, learnings: Path) -> int:
    cfg = _load_sources(sources_path)
    cands: list[tuple[str, str, str]] = []
    for pl in cfg.get("youtube", []):
        for vid, url in en.youtube_items(pl): cands.append(("youtube", vid, url))
    try: cands += _instagram_candidates(cfg.get("instagram") or {})
    except Exception as e: _log(f"instagram skipped — {e}")  # never abort the YouTube half
    seen = st.seen_ids(state_path, out_dir)
    fresh = [(s, i, u) for (s, i, u) in cands if i not in seen]
    _log(f"enumerated {len(cands)} · {len(cands)-len(fresh)} already seen · {len(fresh)} new")
    if not fresh: _log("0 new videos — deck is up to date"); return 0
    ok = 0
    for n, (source, vid, url) in enumerate(fresh, 1):
        _log(f"curating {n}/{len(fresh)} · {source}:{vid}")
        if _curate_one(url, out_dir, learnings) == 0:
            st.mark_seen(state_path, source, vid); ok += 1; _log(f"done {n}/{len(fresh)} · {source}:{vid}")
        else:
            _log(f"FAILED {n}/{len(fresh)} · {source}:{vid} — will retry next import")
    _log(f"import complete · {ok}/{len(fresh)} curated")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser()
    for a in ("sources","out","state","learnings"): ap.add_argument(f"--{a}", required=True)
    a = ap.parse_args()
    return run(Path(a.sources), Path(a.out), Path(a.state), Path(a.learnings))

if __name__ == "__main__": sys.exit(main())
