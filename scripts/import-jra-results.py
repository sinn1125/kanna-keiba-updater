"""Refresh JRA confirmed results from official race-result pages.

The official monthly/day indexes supply the current links. Never infer a
result or final odds from an unconfirmed entry or an earlier race.
"""
import importlib.util
import json
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
        if len(finishers) != 1 or not refund.text():
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
    if not meetings:
        print("RESULTS UNPUBLISHED", date)
        return
    updates = []
    for label, cname in meetings:
        day = p.tree(p.request_jra("S", cname))
        options = links(day, "pw01ses1")
        if len(options) != 1:
            raise ValueError(f"Official result-page link missing for {label}")
        venue = next((v for v in VENUES if v in label), None)
        if not venue:
            raise ValueError(f"Unexpected official venue: {label}")
        updates += parse_day(p.request_jra("S", options[0][1]), date, venue)
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
        listed = [h for h in horses.values() if h['raceId'] == rid]
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
        if len(matched) != len(listed):
            raise ValueError(f"{rid}: official results matched {len(matched)} of {len(listed)} entries")
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
