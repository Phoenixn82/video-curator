import json
from pathlib import Path
import import_state as st

def test_seen_unions_state_and_disk(tmp_path):
    out = tmp_path / "curator"; out.mkdir()
    (out / "yt-AAA.md").write_text("x", encoding="utf-8")
    (out / "ig-BBB.md").write_text("x", encoding="utf-8")
    state = tmp_path / "import-state.json"
    state.write_text(json.dumps({"youtube_seen": ["CCC"], "instagram_seen": []}), encoding="utf-8")
    assert st.seen_ids(state, out) == {"AAA", "BBB", "CCC"}

def test_unseen_filters_known(tmp_path):
    out = tmp_path / "curator"; out.mkdir()
    (out / "yt-AAA.md").write_text("x", encoding="utf-8")
    state = tmp_path / "import-state.json"
    assert st.unseen(["AAA", "DDD", "AAA"], st.seen_ids(state, out)) == ["DDD"]

def test_mark_seen_persists(tmp_path):
    state = tmp_path / "import-state.json"
    st.mark_seen(state, "youtube", "EEE"); st.mark_seen(state, "instagram", "FFF")
    st.mark_seen(state, "youtube", "EEE")  # idempotent
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["youtube_seen"] == ["EEE"] and d["instagram_seen"] == ["FFF"]
