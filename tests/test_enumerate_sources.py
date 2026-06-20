import enumerate_sources as en
def test_parse_flat_playlist_ids():
    assert en.parse_ids("BMMcmmnjrM8\n55pTFVoclvE\n\nUQmmGnz6iVQ\n") == ["BMMcmmnjrM8","55pTFVoclvE","UQmmGnz6iVQ"]
def test_youtube_urls_from_ids(monkeypatch):
    monkeypatch.setattr(en, "_run_ytdlp_ids", lambda u: ["AAA","BBB"])
    assert en.youtube_items("https://youtube.com/playlist?list=X") == [
        ("AAA","https://www.youtube.com/watch?v=AAA"), ("BBB","https://www.youtube.com/watch?v=BBB")]
