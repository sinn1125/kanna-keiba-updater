"""Refresh published JRA histories for horses in the current entry sheets.

Only official horse profile links already recorded by import-jra-entries are
followed. Preserve notes and earlier enrichment while replacing stale starts.
"""
import concurrent.futures
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profiles", ROOT / "scripts/import-profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def main():
    dates = set(sys.argv[1:])
    if not dates:raise ValueError('Specify update date')
    source = ROOT / "dist/results-data.js"
    original = source.read_text()
    data = json.loads(original.removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    races = {r["id"]: r for r in data["races"] if r["date"] in dates}
    horses = [h for h in data["horses"] if h["raceId"] in races and h.get("entryState") != "not_listed" and not h.get("historyComplete")]
    if not horses:
        print('REFRESHED HISTORIES 0 missing horses')
        return
    if not races or any(not h.get("horseCname") for h in horses):
        raise ValueError("Missing official race or horse profile link")

    def load(h):
        cname = h["horseCname"]
        if not re.fullmatch(r"pw01dud\d{11,13}/[0-9A-F]{2}", cname):
            raise ValueError("Unexpected official horse profile link")
        page = profiles.request_jra("U", cname)
        history = profiles.parse_horse(page)
        if not history:
            # A debut runner legitimately has no earlier start.
            if "出走レース" not in page:
                raise ValueError("Missing official horse history")
        prior = [run for run in history if run["date"] < races[h["raceId"]]["date"]]
        if len({(x["date"], x["course"], x["name"]) for x in prior}) != len(prior):
            raise ValueError("Duplicate official horse history")
        return h["id"], prior

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(load, horses))
    for h in horses:
        old = {(x["date"], x["course"], x["name"]): x for x in h.get("history", [])}
        current = []
        for run in results[h["id"]]:
            previous = old.get((run["date"], run["course"], run["name"]), {})
            current.append({**run, **{k: v for k, v in previous.items() if k not in run or not run[k]}})
        h["history"] = current
        h["historyComplete"] = True
        h["historySource"] = "JRA公式競走馬情報・全出走レース（中央競馬）"
    updated = "const RESULT_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    if updated != original:
        source.write_text(updated)
    print("REFRESHED HISTORIES", len(races), "races", len(horses), "horses", sum(map(len, results.values())), "prior starts", "CHANGED", updated != original)


if __name__ == "__main__":
    main()
