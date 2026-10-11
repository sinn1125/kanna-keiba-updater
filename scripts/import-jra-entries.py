"""Import this week's published JRA entries, preserving unannounced fields.

The official entry index supplies meeting links; no CNAME or meeting date is
hard-coded. Horse identity is based on the official horse CNAME, not a draw.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profiles", ROOT / "scripts/import-profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)
VENUE_CODES = dict(zip(profiles.VENUES, ["sapporo", "hakodate", "fukushima", "niigata", "tokyo", "nakayama", "chukyo", "kyoto", "hanshin", "kokura"]))
LINK = re.compile(r"doAction\('/JRADB/accessD.html',\s*'([^']+)'\)")


def official_links(root, prefix):
    out = []
    for a in root.all("a"):
        m = LINK.search(a.a.get("onclick", ""))
        if m and m[1].startswith(prefix):
            out.append((a.text(), m[1]))
    return list(dict.fromkeys(out))


def cell(row, cls):
    return row.first("td", cls)


def parse_meeting(label, cname):
    heading = profiles.tree(profiles.request_jra("D", cname))
    table = [t for t in heading.all("table") if t.a.get("class", "").find("narrow-xy") >= 0]
    if len(table) != 12:
        raise ValueError(f"{label}: expected 12 official races, got {len(table)}")
    races, horses = [], []
    fetched = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    for t in table:
        caption = t.first("caption")
        date_line = caption.first("div", "date").text()
        dm = re.search(r"(\d{4})年(\d+)月(\d+)日.*?(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)", date_line)
        if not dm:
            raise ValueError(f"Unrecognized official meeting: {date_line}")
        date = f"{dm[1]}-{int(dm[2]):02}-{int(dm[3]):02}"
        venue = dm[4]
        number = int(re.search(r"(\d+)レース", caption.first("img").a.get("alt", ""))[1])
        rid = f"jra-{date}-{VENUE_CODES[venue]}-{number}"
        start = re.search(r"(\d+)時(\d+)分", caption.first("div", "time").text())
        course = caption.first("div", "course").text()
        distance = re.search(r"([\d,]+)メートル", course)
        name = caption.first("span", "race_name").text() or caption.first("span", "main").text()
        entry_rows = [row for row in t.all("tr") if cell(row, "horse").text()]
        race = {"id": rid, "date": date, "course": venue, "meeting": re.search(r"(\d+回" + venue + r"\d+日)", date_line)[1],
                "number": number, "name": name, "distance": distance[1].replace(",", "") if distance else "",
                "surface": course, "track": course.split("（")[-1].rstrip("）") if "（" in course else "",
                "time": f"発走時刻：{start[1]}時{start[2]}分" if start else "",
                "startTime": f"{int(start[1]):02}:{int(start[2]):02}" if start else "",
                "category": caption.first("div", "category").text(), "className": caption.first("div", "class").text(),
                "rule": caption.first("div", "rule").text(), "grade": "", "field": len(entry_rows),
                "registeredField": len(entry_rows), "status": "entries", "source": "JRA公式出馬表", "fetchedAt": fetched}
        if not name or not entry_rows:
            raise ValueError(f"{rid}: missing race name or entries")
        races.append(race)
        for row in entry_rows:
            horse_cell = cell(row, "horse")
            a = horse_cell.first("a")
            horse_cname = a.a.get("href", "").split("CNAME=")[-1]
            identity = re.search(r"(\d{10,})", horse_cname)
            horse_name = a.text() or horse_cell.text()
            if not horse_name:
                raise ValueError(f"{rid}: horse name not found")
            # A small number of official entries have no linked horse profile.
            # A name-derived key is still stable when the draw is assigned.
            identity_key = identity[1] if identity else "name" + hashlib.sha256(horse_name.encode()).hexdigest()[:14]
            number_text = cell(row, "num").text()
            frame_cell = cell(row, "waku")
            frame_image = frame_cell.first("img")
            frame_text = frame_cell.text() or frame_image.a.get("alt", "")
            frame_match = re.search(r"(\d+)", frame_text)
            odds_text = cell(row, "odds").text()
            win_odds = float(odds_text) if re.fullmatch(r"\d+(?:\.\d+)?", odds_text) else None
            horses.append({"id": rid + "-horse-" + identity_key, "raceId": rid,
                           "horseCname": horse_cname if identity else "", "name": horse_name,
                           "number": number_text if number_text.isdigit() else "",
                           "frame": int(frame_match[1]) if frame_match else None,
                           "jockey": cell(row, "jockey").text(), "trainer": cell(row, "trainer").text(),
                           "load": cell(row, "weight").text().replace("kg", ""),
                           "age": cell(row, "age").text(), "weight": cell(row, "h_weight").text(),
                           "winOdds": win_odds, "oddsState": "current" if win_odds is not None else "unpublished",
                           "mark": "", "note": "", "history": [], "historyComplete": False,
                           "finish": "", "passing": "", "last3f": "", "time": "", "margin": ""})
    if len({r["id"] for r in races}) != len(races) or len({h["id"] for h in horses}) != len(horses):
        raise ValueError(f"{label}: duplicate race or horse ID")
    print(label, len(races), "races", len(horses), "horses", flush=True)
    return races, horses


def main():
    index = profiles.tree(profiles.request_jra("D", "pw01dli00/F3"))
    meetings = official_links(index, "pw01drl")
    if not meetings:
        raise ValueError("No official meetings published")
    # Follow the actual official link to the full entries for each meeting.
    jobs = []
    for label, cname in meetings:
        page = profiles.tree(profiles.request_jra("D", cname))
        all_races = official_links(page, "pw01des")
        if len(all_races) != 1:
            raise ValueError(f"{label}: official full-entry link missing")
        jobs.append((label, all_races[0][1]))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(lambda pair: parse_meeting(*pair), jobs))
    fresh_races = [r for rs, _ in batches for r in rs]
    fresh_horses = [h for _, hs in batches for h in hs]
    target_dates = sorted({r["date"] for r in fresh_races})
    if "--through" in sys.argv:
        through = sys.argv[sys.argv.index("--through") + 1]
        datetime.fromisoformat(through)
        fresh_races = [r for r in fresh_races if r["date"] <= through]
        ids = {r["id"] for r in fresh_races}
        fresh_horses = [h for h in fresh_horses if h["raceId"] in ids]
        target_dates = sorted({r["date"] for r in fresh_races})
    if ("--through" not in sys.argv and len(fresh_races) != len(meetings) * 12) or len({h["id"] for h in fresh_horses}) != len(fresh_horses):
        raise ValueError("Incomplete or duplicated official entries")
    target = ROOT / "dist/results-data.js"
    before = target.read_text()
    payload = json.loads(before.removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    old_races = {r["id"]: r for r in payload["races"]}
    old_horses = {h["id"]: h for h in payload["horses"]}
    refreshed_race_ids = {r["id"] for r in fresh_races}
    refreshed_horse_ids = {h["id"] for h in fresh_horses}
    # Keep the stable horse ID and any saved notes if a horse disappears from
    # the latest official entry sheet, but do not count or number it as a starter.
    for old in old_horses.values():
        if (old["raceId"] in refreshed_race_ids and old["id"] not in refreshed_horse_ids
                and old_races.get(old["raceId"], {}).get("status") != "results" and not old.get("finish")):
            old.update({"number": "", "frame": None, "winOdds": None,
                        "oddsState": "unpublished", "entryState": "not_listed"})
    for r in fresh_races:
        old = old_races.get(r["id"], {})
        if old.get("status") == "results":
            continue
        if all(old.get(k) == v for k, v in r.items() if k != "fetchedAt"):
            r["fetchedAt"] = old.get("fetchedAt", r["fetchedAt"])
        old_races[r["id"]] = {**old, **r}
    for h in fresh_horses:
        old = old_horses.get(h["id"], {})
        if old.get("finish") or old_races.get(h["raceId"], {}).get("status") == "results":
            continue
        old_horses[h["id"]] = {**h, **{k: v for k, v in old.items() if k not in ("number", "frame", "weight", "winOdds", "oddsState", "jockey", "trainer", "load", "age")}}
        old_horses[h["id"]].update({k: h[k] for k in ("number", "frame", "weight", "winOdds", "oddsState", "jockey", "trainer", "load", "age")})
        old_horses[h["id"]]["entryState"] = "listed"
    assert len(old_races) == len(set(old_races)) and len(old_horses) == len(set(old_horses))
    after = "const RESULT_DATA=" + json.dumps({"races": list(old_races.values()), "horses": list(old_horses.values())}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    if before != after:
        target.write_text(after)
    print("DATES", ",".join(target_dates), "RACES", len(fresh_races), "HORSES", len(fresh_horses), "CHANGED", before != after)


if __name__ == "__main__":
    main()
