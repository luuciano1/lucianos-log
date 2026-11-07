"""Pull new Letterboxd diary entries (from the public RSS feed) into entries.json.

Only diary items never seen before are added, and a film that's already on the
site is skipped, so manual edits and deletions on the site stick.
TMDB_TOKEN (optional repo secret) fills in release date, director and genre.
"""
import os, re, sys, hashlib, html
import xml.etree.ElementTree as ET

from common import (session, load_entries, save_entries, load_state, save_state,
                    save_art, now_ms, warn, norm_title)

USER = os.environ.get("LETTERBOXD_USER", "luucianoo")
TMDB = os.environ.get("TMDB_TOKEN", "").strip()
STATE = "letterboxd.json"
NS = {"letterboxd": "https://letterboxd.com", "tmdb": "https://themoviedb.org"}


def tmdb_info(s, movie_id):
    if not (TMDB and movie_id):
        return {}
    try:
        r = s.get(f"https://api.themoviedb.org/3/movie/{movie_id}",
                  params={"append_to_response": "credits"},
                  headers={"Authorization": f"Bearer {TMDB}"}, timeout=30)
        if r.status_code != 200:
            return {}
        j = r.json()
        directors = [c["name"] for c in j.get("credits", {}).get("crew", []) if c.get("job") == "Director"]
        return {"released": j.get("release_date") or "",
                "genre": (j.get("genres") or [{}])[0].get("name", ""),
                "creator": ", ".join(directors),
                "poster": ("https://image.tmdb.org/t/p/w500" + j["poster_path"]) if j.get("poster_path") else ""}
    except Exception as e:
        print("  TMDB lookup failed:", e)
        return {}


def review_text(desc):
    """RSS description is HTML: a poster <p><img></p> then the review paragraphs."""
    paras = re.findall(r"<p>(.*?)</p>", desc or "", flags=re.S)
    out = []
    for p in paras:
        if "<img" in p:
            continue
        t = html.unescape(re.sub(r"<br\s*/?>", "\n", p))
        t = re.sub(r"<[^>]+>", "", t).strip()
        if t and not re.match(r"^Watched on ", t):
            out.append(t)
    return "\n\n".join(out)


def main():
    s = session()
    try:
        r = s.get(f"https://letterboxd.com/{USER}/rss/", timeout=30)
    except Exception as e:
        warn(f"Couldn't reach Letterboxd ({e}). Nothing synced this run.")
        return 0
    if r.status_code != 200:
        warn(f"Letterboxd answered {r.status_code}. Nothing synced this run.")
        return 0

    data = load_entries()
    entries = data["entries"]
    state = load_state(STATE) or {"seen": []}
    seen = set(state["seen"])
    on_site = {(norm_title(e["title"]), (e.get("released") or "")[:4])
               for e in entries if e.get("medium") == "film"}
    on_site_titles = {t for t, _ in on_site}

    items = ET.fromstring(r.content).findall("./channel/item")
    added = 0
    for it in reversed(items):  # oldest first
        guid = it.findtext("guid") or ""
        if not guid or guid in seen:
            continue
        seen.add(guid)
        title = it.findtext("letterboxd:filmTitle", "", NS)
        if not title:  # lists etc., not diary entries
            continue
        year = it.findtext("letterboxd:filmYear", "", NS)
        key = norm_title(title)
        if (key, year) in on_site or (key in on_site_titles and not year):
            continue
        # site entries made by hand sometimes have no release date; match on title then
        if any(t == key and y == "" for t, y in on_site):
            continue
        print(f"+ {title} ({year})")
        t = tmdb_info(s, it.findtext("tmdb:movieId", "", NS))
        desc = it.findtext("description") or ""
        img = re.search(r'<img src="([^"]+)"', desc)
        art_id = hashlib.md5(guid.encode()).hexdigest()
        art = save_art(s, t.get("poster") or (img.group(1) if img else ""), art_id) if (t.get("poster") or img) else ""
        rating = it.findtext("letterboxd:memberRating", "", NS)
        entries.insert(0, {
            "id": "lbx" + re.sub(r"\D", "", guid)[:20], "medium": "film",
            "title": title, "creator": t.get("creator", ""), "kind": "Feature",
            "released": t.get("released", ""), "genre": t.get("genre", ""),
            "rating": float(rating) if rating else 0, "review": review_text(desc),
            "loggedOn": it.findtext("letterboxd:watchedDate", "", NS),
            "again": it.findtext("letterboxd:rewatch", "", NS) == "Yes",
            "year": "", "subgenres": [], "createdAt": now_ms(), "updatedAt": now_ms(), "art": art,
        })
        on_site.add((key, (t.get("released") or year)[:4]))
        added += 1

    state["seen"] = sorted(seen)
    save_state(STATE, state)
    if added:
        save_entries(data)
    print(f"Added {added} film(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
