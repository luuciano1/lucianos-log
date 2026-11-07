"""Pull new ratings from Album of the Year into entries.json.

Only albums never seen before are added, so edits you make on the site (and
albums you delete from it) are left alone. If AOTY refuses the request, the run
just logs a warning and changes nothing.
"""
import os, re, sys, json, html, datetime
from collections import Counter, defaultdict

from bs4 import BeautifulSoup
from common import (session, load_entries, save_entries, load_state, save_state,
                    save_art, now_ms, warn)

USER = os.environ.get("AOTY_USER", "iuciano")
BASE = "https://www.albumoftheyear.org"
STATE = "aoty.json"
KIND = {"lp": "Album", "ep": "EP", "single": "Single", "mixtape": "Mixtape"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def to_stars(score):
    """AOTY 0-100 -> 0-5 in half stars (73 -> 3.5, 85 -> 4.5)."""
    return max(0.0, min(5.0, int(score / 10.0 + 0.5) / 2.0))


def rated_on(text, today):
    """'9h ago' / '3d ago' / 'Nov 4' / 'Nov 4, 2025' -> YYYY-MM-DD."""
    t = (text or "").strip().lower()
    m = re.match(r"(\d+)\s*(m|min|h|hr|d|day)s?\s*ago", t)
    if m:
        days = int(m.group(1)) if m.group(2).startswith("d") else 0
        return (today - datetime.timedelta(days=days)).isoformat()
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?", t)
    if m and m.group(1) in MONTHS:
        y = int(m.group(3)) if m.group(3) else today.year
        d = datetime.date(y, MONTHS[m.group(1)], int(m.group(2)))
        if d > today:
            d = d.replace(year=y - 1)
        return d.isoformat()
    return today.isoformat()


def parse_ratings(page):
    out = []
    for b in BeautifulSoup(page, "html.parser").select(".albumBlock"):
        a = b.select_one('a[href^="/album/"]')
        sc = b.select_one(".rating")
        if not a or not sc:
            continue
        try:
            score = float(sc.get_text(strip=True))
        except ValueError:
            continue
        typ = (b.select_one(".type") or sc).get_text(" ", strip=True)
        out.append({
            "aid": re.match(r"/album/(\d+)", a["href"]).group(1),
            "href": a["href"].split("?")[0], "score": score,
            "when": (b.select_one(".ratingText") or sc).get_text(strip=True),
            "fmt": typ.split("•")[-1].strip().lower() if "•" in typ else "",
            "title": (b.select_one(".albumTitle") or a).get_text(strip=True),
            "artist": (b.select_one(".artistTitle") or a).get_text(strip=True),
        })
    return out


def parse_album(page):
    soup = BeautifulSoup(page, "html.parser")
    info = {}
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            ld = json.loads(tag.string or "")
        except Exception:
            continue
        if isinstance(ld, dict) and ld.get("@type") == "MusicAlbum":
            info["title"] = html.unescape(ld.get("name", ""))
            by = ld.get("byArtist") or {}
            by = by if isinstance(by, list) else [by]
            info["artist"] = ", ".join(html.unescape(x.get("name", "")) for x in by if x.get("name"))
            info["released"] = (ld.get("datePublished") or "")[:10]
            info["image"] = ld.get("image") or ""
    genres, fmt = [], ""
    for row in soup.select(".detailRow"):
        label = (row.select_one("span") or row).get_text(" ", strip=True).lower()
        if "genre" in label:
            genres = [a.get_text(strip=True) for a in row.select('a[href^="/genre/"]')]
        elif "format" in label:
            fmt = row.get_text(" ", strip=True).split("/")[0].strip().lower()
    info["genres"] = [g for g in dict.fromkeys(genres) if g]
    info["fmt"] = fmt
    if not info.get("image"):
        og = soup.select_one('meta[property="og:image"]')
        info["image"] = og["content"] if og else ""
    return info


def learned_parents(entries):
    """Learn sub-genre -> main genre from the albums already on the site."""
    votes = defaultdict(Counter)
    for e in entries:
        if e.get("medium") == "music" and e.get("genre"):
            for i, sg in enumerate(e.get("subgenres") or []):
                votes[sg][e["genre"]] += 3 if i == 0 else 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


KEYWORDS = [("hip hop", "Hip Hop"), ("rap", "Hip Hop"), ("trap", "Hip Hop"), ("drill", "Hip Hop"),
            ("r&b", "R&B"), ("soul", "R&B"), ("house", "Dance"), ("techno", "Dance"),
            ("dance", "Dance"), ("electro", "Electronic"), ("ambient", "Ambient"),
            ("jazz", "Jazz"), ("folk", "Folk"), ("country", "Country"), ("metal", "Metal"),
            ("punk", "Punk"), ("rock", "Rock"), ("pop", "Pop"), ("singer", "Singer-Songwriter"),
            ("gospel", "Gospel"), ("classical", "Classical"), ("psych", "Psychedelia")]


def main_genre(subs, learned):
    for sg in subs:
        if sg in learned:
            return learned[sg]
    for sg in subs:
        for k, g in KEYWORDS:
            if k in sg.lower():
                return g
    return ""


def main():
    s = session()
    data = load_entries()
    entries = data["entries"]
    state = load_state(STATE) or {"seen": []}
    seen = set(state["seen"]) | {e["id"][4:] for e in entries if e["id"].startswith("aoty")}

    try:
        r = s.get(f"{BASE}/user/{USER}/ratings/", timeout=30)
    except Exception as e:
        warn(f"Couldn't reach AOTY ({e}). Nothing synced this run.")
        return 0
    if r.status_code != 200 or "albumBlock" not in r.text:
        warn(f"AOTY answered {r.status_code} instead of your ratings page "
             "(it may not allow requests from GitHub's servers). Nothing synced this run.")
        return 0

    rows = parse_ratings(r.text)
    print(f"Found {len(rows)} ratings on the first page")
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date()
    learned = learned_parents(entries)
    added = 0
    for x in reversed([x for x in rows if x["aid"] not in seen]):  # oldest first
        print(f"+ {x['artist']} - {x['title']} ({x['score']:g})")
        info = {}
        try:
            ar = s.get(BASE + x["href"], timeout=30)
            if ar.status_code == 200:
                info = parse_album(ar.text)
            else:
                print(f"  album page answered {ar.status_code}; using ratings-page info only")
        except Exception as e:
            print("  album page failed:", e)
        fmt = (info.get("fmt") or x["fmt"]).split()
        subs = info.get("genres", [])
        rel = info.get("released", "")
        art = save_art(s, info["image"], "a07e" + x["aid"].zfill(28)) if info.get("image") else ""
        entries.insert(0, {
            "id": "aoty" + x["aid"], "medium": "music",
            "title": info.get("title") or x["title"],
            "creator": info.get("artist") or x["artist"],
            "kind": KIND.get(fmt[0] if fmt else "", "Album"),
            "released": rel, "genre": main_genre(subs, learned),
            "rating": to_stars(x["score"]), "review": "",
            "loggedOn": rated_on(x["when"], today), "again": False,
            "year": rel[:4] if re.match(r"\d{4}", rel) else "",
            "subgenres": subs, "createdAt": now_ms(), "updatedAt": now_ms(), "art": art,
        })
        seen.add(x["aid"])
        added += 1

    state["seen"] = sorted(seen, key=int)
    save_state(STATE, state)
    if added:
        save_entries(data)
    print(f"Added {added} album(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
