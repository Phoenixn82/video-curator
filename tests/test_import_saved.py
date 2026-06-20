import json
import import_saved as imp

def test_curates_only_unseen_and_marks(tmp_path, monkeypatch, capsys):
    out = tmp_path / "curator"; out.mkdir()
    (out / "yt-AAA.md").write_text("x", encoding="utf-8")  # already curated
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"youtube":["pl"],"instagram":{}}), encoding="utf-8")
    state = tmp_path / "state.json"
    monkeypatch.setattr(imp.en, "youtube_items", lambda u: [("AAA","uAAA"),("BBB","uBBB")])
    calls=[]; monkeypatch.setattr(imp, "_curate_one", lambda url,o,l: (calls.append(url) or 0))
    assert imp.run(src, out, state, tmp_path/"l.md") == 0
    assert calls == ["uBBB"]
    assert "BBB" in json.loads(state.read_text(encoding="utf-8"))["youtube_seen"]
    assert "[import]" in capsys.readouterr().out

def test_curate_failure_leaves_unseen(tmp_path, monkeypatch):
    out = tmp_path / "curator"; out.mkdir()
    src = tmp_path / "src.json"; src.write_text(json.dumps({"youtube":["pl"],"instagram":{}}), encoding="utf-8")
    state = tmp_path / "state.json"
    monkeypatch.setattr(imp.en, "youtube_items", lambda u: [("BBB","uBBB")])
    monkeypatch.setattr(imp, "_curate_one", lambda url,o,l: 1)
    imp.run(src, out, state, tmp_path/"l.md")
    d = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {"youtube_seen":[]}
    assert "BBB" not in d.get("youtube_seen", [])

def test_instagram_candidates_feed_loop(tmp_path, monkeypatch):
    out = tmp_path / "curator"; out.mkdir()
    src = tmp_path / "src.json"; src.write_text(json.dumps({"youtube":[],"instagram":{"collection_url":"u"}}), encoding="utf-8")
    state = tmp_path / "state.json"
    monkeypatch.setattr(imp.en, "youtube_items", lambda u: [])
    monkeypatch.setattr(imp, "_instagram_candidates", lambda c: [("instagram","SC1","https://www.instagram.com/p/SC1/")])
    called=[]; monkeypatch.setattr(imp, "_curate_one", lambda url,o,l: (called.append(url) or 0))
    imp.run(src, out, state, tmp_path/"l.md")
    assert called == ["https://www.instagram.com/p/SC1/"]
