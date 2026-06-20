# ig_collection.py - read shortcodes from a specific IG saved collection using the existing
# Chrome session (headless, nothing opened). Raises on block/expiry (caller logs, never aborts).
from pathlib import Path
from playwright.sync_api import sync_playwright
import browser_cookie3
import os
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def _as_playwright_cookies(cj):
    return [{"name": c.name, "value": c.value, "domain": c.domain,
             "path": c.path or "/", "secure": bool(c.secure)} for c in cj]

def _profile_cookie_files():
    explicit = os.environ.get("IG_COOKIE_FILE") or os.environ.get("INSTAGRAM_COOKIE_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            yield p

    profile_dir = os.environ.get("IG_CHROME_PROFILE_DIR") or os.environ.get("VIDEO_WATCH_INSTAGRAM_PROFILE")
    if profile_dir:
        p = Path(profile_dir).expanduser() / "Network" / "Cookies"
        if p.exists():
            yield p

    root = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    for p in [root / "Default", *sorted(root.glob("Profile *"))]:
        cookie_file = p / "Network" / "Cookies"
        if cookie_file.exists():
            yield cookie_file

def _cookies():
    errors = []
    for cookie_file in [None, *_profile_cookie_files()]:
        try:
            cj = browser_cookie3.chrome(
                cookie_file=str(cookie_file) if cookie_file else None,
                domain_name="instagram.com"
            )
            cookies = _as_playwright_cookies(cj)
            if cookies:
                return cookies
        except Exception as e:
            label = str(cookie_file) if cookie_file else "default Chrome profile"
            errors.append(f"{label}: {type(e).__name__}: {e}")
    detail = f" ({'; '.join(errors)})" if errors else ""
    raise RuntimeError(f"no Instagram cookies in Chrome - sign into Instagram in Chrome{detail}")

def collection_shortcodes(collection_url: str, max_scrolls: int = 40) -> list[str]:
    cookies = _cookies()
    found, seen = [], set()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA); ctx.add_cookies(cookies)
        page = ctx.new_page(); page.goto(collection_url, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        if page.query_selector('input[name="username"]'):
            b.close(); raise RuntimeError("Instagram session not accepted (expired/blocked) - re-open Instagram in Chrome")
        last = -1
        for _ in range(max_scrolls):
            for a in page.query_selector_all('a[href*="/p/"], a[href*="/reel/"]'):
                h = a.get_attribute("href") or ""
                for m in ("/p/", "/reel/"):
                    if m in h:
                        sc = h.split(m)[1].split("/")[0]
                        if sc not in seen: seen.add(sc); found.append(sc)
            if len(found) == last: break
            last = len(found); page.mouse.wheel(0, 4000); page.wait_for_timeout(1500)
        b.close()
    return found
