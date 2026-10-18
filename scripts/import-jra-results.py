"""Refresh JRA confirmed results from official race-result pages.

The official monthly/day indexes supply the current links. Never infer a
result or final odds from an unconfirmed entry or an earlier race.
"""
import importlib.util
import concurrent.futures
import os
import json
import copy
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profiles", ROOT / "scripts/import-profiles.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
VENUES = dict(zip(p.VENUES, ["sapporo", "hakodate", "fukushima", "niigata", "tokyo", "nakayama", "chukyo", "kyoto", "hanshin", "kokura"]))
LINK = re.compile(r"doAction\('/JRADB/accessS.html',\s*'([^']+)'\)")


def links(root, prefix):
    return [(a.text(), m[1]) for a in root.all("a") if (m := LINK.search(a.a.get("onclick", ""))) and m[1].startswith(prefix)]


def normalize(value):
    return re.sub(r"\s+", "", value or "")


def parse_day(source, date, venue):
    root = p.tree(source)
    units = root.all("div", "race_result_unit")
    updates = []
    for unit in units:
        header = unit.first("div", "race_header")
        number_image = header.first("img")
        number_match = re.search(r"(\d+)レース", number_image.a.get("alt", ""))
        if not number_match:
            continue
        number = int(number_match[1])
        rid = f"jra-{date}-{VENUES[venue]}-{number}"
        result_table = unit.first("table", "basic")
        rows = result_table.first("tbody").all("tr")
        finishers = [row for row in rows if row.first("td", "place").text() == "1"]
        refund = unit.first("div", "refund_unit")
        if not finishers or not refund.text():
            continue
        payout = []
        for li in refund.all("li"):
            typ = re.match(r"(?:3連複|3連単|単勝|複勝|枠連|馬連|馬単|ワイド)", li.text())
            if not typ:
                continue
            for pick, yen, rank in re.findall(r"(\d+(?:-\d+){0,2})\s+([\d,]+)円\s+(\d+)番人気", li.text()):
                payout.append({"type": typ[0], "pick": pick, "amount": int(yen.replace(",", "")), "popularity": rank + "番人気"})
        if not payout or not any(x['type'] == '単勝' for x in payout):
            raise ValueError(f"{rid}: result visible but official payouts incomplete")
        laps = []
        for row in unit.first("div", "result_time_data").all("tr"):
            cs = p.cells(row)
            if len(cs) == 2 and "ハロンタイム" in cs[0].text():
                laps = [float(x) for x in re.findall(r"\d+\.\d+", cs[1].text())]
        corners = []
        for row in unit.first("div", "result_corner_place").all("tr"):
            cs = p.cells(row)
            if len(cs) == 2 and cs[0].text() and cs[1].text():
                corners.append({"corner": cs[0].text(), "order": cs[1].text()})
        updates.append((rid, {"status": "results", "payouts": payout, "laps": laps, "cornerOrders": corners,
                              "weather": header.first("div", "baba").text(), "source": "JRA公式レース結果",
                              "fetchedAt": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")}, rows))
    return updates


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    datetime.fromisoformat(date)
    ym = date[:7].replace("-", "")
    index = p.request_jra("S", "pw01skl00999999/B3")
    params = dict(re.findall(r'objParam\["(\d+)"\]="([A-F0-9]+)"', index))
    current_month = re.search(r'var yearMonth = "(\d{6})"', index)[1]
    if ym[2:] not in params:
        print("RESULTS UNPUBLISHED", date)
        return
    month_cname = ("pw01skl00" if ym >= current_month else "pw01skl10") + ym + "/" + params[ym[2:]]
    month = p.tree(p.request_jra("S", month_cname))
    meetings = [(label, cn) for label, cn in links(month, "pw01srl1") if date.replace("-", "") in cn]
    updates = []
    if not meetings and '--seed' not in sys.argv:
        # This is the current-results navigation link observed on JRA.
        current = p.request_jra('S', 'pw01sli00/AF')
        observed = re.findall(r"pw01sde01\d+/[A-F0-9]{2}", current)
        seed = next((cn for cn in observed if date.replace('-', '') in cn), None)
        if seed:
            sys.argv.extend(['--seed', seed])
    if meetings:
        for label, cname in meetings:
            day = p.tree(p.request_jra("S", cname))
            options = links(day, "pw01ses1")
            if len(options) != 1:
                raise ValueError(f"Official result-page link missing for {label}")
            venue = next((v for v in VENUES if v in label), None)
            if not venue:
                raise ValueError(f"Unexpected official venue: {label}")
            updates += parse_day(p.request_jra("S", options[0][1]), date, venue)
    elif "--seed" in sys.argv:
        # The monthly index can lag behind results published during a meeting.
        # Start from an independently observed official individual result URL,
        # then follow the actual day and race links present in JRA navigation.
        seed = sys.argv[sys.argv.index("--seed") + 1]
        if not re.fullmatch(r"pw01sde0\d+/[A-F0-9]{2}", seed) or date.replace("-", "") not in seed:
            raise ValueError("Seed is not an official result link for the target date")
        seed_html = p.request_jra("S", seed)
        seed_links = re.findall(r'href="/JRADB/accessS\.html\?CNAME=(pw01sde0[^"<>]+)"', seed_html)
        by_venue = {}
        for cn in [seed] + seed_links:
            match = re.match(r'pw01sde01(\d{2})', cn)
            if match and date.replace("-", "") in cn:
                by_venue.setdefault(match[1], cn)
        days = []
        for cn in by_venue.values():
            page = p.tree(seed_html if cn == seed else p.request_jra("S", cn))
            days += [day for _, day in links(page, "pw01srl0") if date.replace("-", "") in day]
        days = list(dict.fromkeys(days))
        if not days:
            raise ValueError("No official meeting links on the result page")
        jobs = []
        for day_cname in days:
            day = p.tree(p.request_jra("S", day_cname))
            heading = next((h.text() for h in day.all("h1") if "レース結果 レース選択" in h.text()), "")
            venue = next((v for v in VENUES if v in heading), None)
            if not venue or date.replace("-", "") not in day_cname:
                raise ValueError("Official result meeting/date mismatch")
            # href CNAMEs are actual links on the official day page. Future
            # races may appear there but yield no confirmed result/payout.
            page = p.request_jra("S", day_cname)
            race_links = list(dict.fromkeys(re.findall(r'href="/JRADB/accessS\.html\?CNAME=(pw01sde0[^"<>]+)"', page)))
            jobs += [(venue, cn) for cn in race_links if date.replace("-", "") in cn]
        if not jobs:
            raise ValueError("No official individual race links")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(4,int(os.environ.get("JRA_FETCH_WORKERS","4"))))) as pool:
            for batch in pool.map(lambda job: parse_day(p.request_jra("S", job[1]), date, job[0]), jobs):
                updates += batch
    else:
        print("RESULTS UNPUBLISHED", date)
        return
    if len({rid for rid, _, _ in updates}) != len(updates):
        raise ValueError("Duplicate result race ID")
    target = ROOT / "dist/results-data.js"
    before = target.read_text()
    data = json.loads(before.removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    races = {r["id"]: r for r in data["races"]}
    horses = {h["id"]: h for h in data["horses"]}
    count = 0
    for rid, result, rows in updates:
        if rid not in races:
            raise ValueError(f"Result has no registered race: {rid}")
        listed = [copy.deepcopy(h) for h in horses.values() if h['raceId'] == rid]
        active_ids = {h['id'] for h in listed if h.get('entryState') != 'not_listed'}
        by_number = {h.get('number'): h for h in listed if h.get('number')}
        by_name = {normalize(h['name']): h for h in listed}
        matched = set()
        for row in rows:
            num = row.first("td", "num").text()
            name = row.first("td", "horse").text()
            if not name:
                continue
            horse = by_number.get(num) or by_name.get(normalize(name))
            if not horse or horse['id'] in matched:
                raise ValueError(f"{rid}: unmatched or duplicate result horse {num} {name}")
            matched.add(horse['id'])
            finish = row.first("td", "place").text()
            clock = row.first("td", "time").text()
            weight = row.first("td", "h_weight").text()
            wm = re.match(r"(\d+)(\([^)]*\))?", weight)
            horse.update({"number": num, "finish": finish, "time": clock, "margin": row.first("td", "margin").text(),
                          "passing": '-'.join(row.first("td", "corner").text().split()),
                          "last3f": row.first("td", "f_time").text(), "popularity": row.first("td", "pop").text(),
                          "weight": wm[1] if wm else weight, "weightChange": wm[2] if wm and wm[2] else "",
                          "jockey": row.first("td", "jockey").text(), "trainer": row.first("td", "trainer").text(),
                          "isRecord": "レコード" in clock})
        if not active_ids.issubset(matched):
            print(f"PENDING {rid}: complete results unavailable ({len(active_ids & matched)}/{len(active_ids)} entries)")
            continue
        for horse in listed:
            horses[horse['id']] = horse
        data['horses'] = list(horses.values())
        old = races[rid]
        if all(old.get(k) == v for k, v in result.items() if k != 'fetchedAt'):
            result['fetchedAt'] = old.get('fetchedAt', result['fetchedAt'])
        races[rid].update(result)
        count += 1
    after = "const RESULT_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    if '--dry-run' not in sys.argv and before != after:
        target.write_text(after)
    print("RESULTS", date, count, "CHANGED", before != after, "DRY_RUN", '--dry-run' in sys.argv)


if __name__ == "__main__":
    main()

