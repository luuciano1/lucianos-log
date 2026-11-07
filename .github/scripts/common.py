"""Shared helpers for the luciano's log sync scripts."""
import json, os, time

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENTRIES = os.path.join(ROOT, "entries.json")
ART = os.path.join(ROOT, "art")
STATE_DIR = os.path.join(ROOT, ".sync")
# Identify ourselves honestly as a small personal sync script.
UA = "lucianos-log-sync/1.0 (+https://github.com/luuciano1/lucianos-log)"


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def load_entries():
    with open(ENTRIES, encoding="utf-8") as f:
        return json.load(f)


def save_entries(data):
    with open(ENTRIES, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def load_state(name):
    p = os.path.join(STATE_DIR, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(name, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def save_art(s, url, art_id):
    """Download a cover to art/<art_id>.jpg. Returns art_id, or "" on failure."""
    try:
        r = s.get(url, timeout=30)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return ""
        os.makedirs(ART, exist_ok=True)
        with open(os.path.join(ART, art_id + ".jpg"), "wb") as f:
            f.write(r.content)
        return art_id
    except Exception as e:
        print("  cover download failed:", e)
        return ""


def now_ms():
    return int(time.time() * 1000)


def warn(msg):
    print(f"::warning::{msg}")


def norm_title(t):
    return "".join(ch for ch in (t or "").lower() if ch.isalnum())
