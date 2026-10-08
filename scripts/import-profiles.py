"""Import public JRA horse histories and person statistics into the private notebook.

Raw response caches stay outside the published directory. Unknown values remain null.
"""
from html.parser import HTMLParser
from pathlib import Path
import concurrent.futures
import json
import re
import subprocess
import sys
import gzip
import http.client
import os
import threading
import urllib.parse
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT.parent / "profile-cache"
CACHE.mkdir(exist_ok=True)
VENUES = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
CONNECTIONS = threading.local()
YEARS = [2026, 2025, 2024]
STATS_THROUGH = "2026-10-04"
FETCHED_AT = "2026-10-06"


def request_jra(kind, cname):
    path = "/JRADB/access" + kind + ".html"
    method, body, headers = "POST", urllib.parse.urlencode({"cname": cname}), {"Content-Type": "application/x-www-form-urlencoded", "Accept-Encoding": "gzip"}
    if kind == "U":
        path += "?CNAME=" + cname
        method, body = "GET", None
    for attempt in range(2):
        connection = getattr(CONNECTIONS, "connection", None)
        if connection is None:
            proxy = urllib.parse.urlparse(os.environ.get("HTTPS_PROXY", ""))
            connection = http.client.HTTPSConnection(proxy.hostname or "www.jra.go.jp", proxy.port or 443, timeout=55)
            if proxy.hostname:
                connection.set_tunnel("www.jra.go.jp", 443)
            CONNECTIONS.connection = connection
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise ValueError("JRA response status " + str(response.status))
            if response.getheader("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data.decode("cp932", errors="replace")
        except (http.client.HTTPException, OSError, ValueError):
            connection.close()
            CONNECTIONS.connection = None
            if attempt:
                raise


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.a, self.children = tag, dict(attrs), []

    def text(self):
        return " ".join("".join(c if isinstance(c, str) else c.text() for c in self.children).split())

    def all(self, tag=None, cls=None):
        out = []
        for c in self.children:
            if isinstance(c, Node):
                if (not tag or c.tag == tag) and (not cls or cls in c.a.get("class", "").split()):
                    out.append(c)
                out += c.all(tag, cls)
        return out

    def first(self, tag=None, cls=None):
        return next(iter(self.all(tag, cls)), Node())


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in ["img", "input", "br", "hr", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param"]:
            self.stack.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def tree(text):
    p = Parser()
    p.feed(text)
    return p.root


def cells(row):
    return [c for c in row.children if isinstance(c, Node) and c.tag in ("th", "td")]


def fetch(kind, cname, label):
    target = CACHE / (label + ".html")
    if target.exists() and not any(x in target.read_text()[:1200] for x in ["Forbidden", "パラメータエラー"]) and len(target.read_text()) > 30000:
        return target.read_text()
    text = request_jra(kind, cname)
    if any(x in text[:1200] for x in ["Forbidden", "パラメータエラー"]) or len(text) < 30000:
        raise ValueError("Missing public JRA data: " + label)
    temporary = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(text)
    temporary.replace(target)
    return text


def entry_index():
    horses, people = {}, {"jockey": {}, "trainer": {}}
    saved_index = ROOT / "scripts/source-index.json"
    if not (ROOT.parent / "entry-tokyo-1.html").exists():
        index = json.loads(saved_index.read_text())
        (CACHE / "index.json").write_text(json.dumps(index, ensure_ascii=False))
        return index["horses"], index["people"]
    data = json.loads((ROOT / "dist/results-data.js").read_text().removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    for code in ["tokyo", "kyoto"]:
        for num in range(1, 13):
            root = tree((ROOT.parent / f"entry-{code}-{num}.html").read_text())
            for row in root.all("tr"):
                hn = row.first("td", "horse")
                n = row.first("td", "num").text()
                link = hn.first("div", "name").first("a")
                href = link.a.get("href", "")
                if not n or "accessU.html?CNAME=" not in href:
                    continue
                hid = f"jra-2026-10-04-{code}-{num}-horse-{n}"
                if not n.isdecimal():
                    hid = next(h["id"] for h in data["horses"] if h["raceId"] == f"jra-2026-10-04-{code}-{num}" and h["name"] == link.text())
                horses[hid] = {"name": link.text(), "cname": href.split("CNAME=")[1]}
                for role, a in [("trainer", hn.first("p", "trainer").first("a")), ("jockey", row.first("td", "jockey").first("p", "jockey").first("a"))]:
                    match = re.search(r"'(pw0[45][^']+)'", str(a.a))
                    if match:
                        people[role][a.text()] = match[1]
    index_text = json.dumps({"horses": horses, "people": people}, ensure_ascii=False)
    (CACHE / "index.json").write_text(index_text)
    saved_index.write_text(index_text)
    return horses, people


def parse_horse(text):
    root = tree(text)
    table = next((t for t in root.all("table") if "出走レース" in t.text()[:100]), None)
    if not table:
        raise ValueError("No horse results table")
    history = []
    for row in table.first("tbody").all("tr"):
        c = cells(row)
        if len(c) < 14:
            continue
        date = re.search(r"(\d{4})年(\d+)月(\d+)日", c[0].text())
        if not date:
            continue
        course = c[1].text()
        if course not in VENUES:
            continue
        track = re.search(r"(芝|ダート|ダ|障害)(\d+)", c[3].text())
        link = c[2].first("a").a.get("href", "")
        grade = c[2].first("img").a.get("alt", "")
        if not grade and re.search(r"オープン|OP", c[2].text()):
            grade = "OP"
        history.append({"date": f"{date[1]}-{int(date[2]):02}-{int(date[3]):02}", "course": course, "name": c[2].first("a").text() or c[2].text(), "grade": grade, "distance": track[2] if track else re.sub(r"\D", "", c[3].text()), "surface": ("障害" if "障" in c[3].text() else track[1] if track else "障害"), "going": c[4].text(), "field": c[5].text(), "popularity": c[6].text(), "finish": c[7].text(), "jockey": c[8].text(), "load": c[9].text(), "weight": c[10].text(), "time": c[11].text(), "rating": c[12].text(), "winner": c[13].text(), "raceCname": link.split("CNAME=")[-1] if "CNAME=" in link else "", "passing": "", "last3f": "", "margin": ""})
    return history


def import_horses():
    index, people = entry_index()
    source = ROOT / "dist/results-data.js"
    d = json.loads(source.read_text().removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    old = {h["id"]: h for h in d["horses"]}
    assert len(index) == len(old), (len(index), len(old))

    def one(item):
        hid, info = item
        history = parse_horse(fetch("U", info["cname"], "horse-" + re.sub(r"\W", "", info["cname"])))
        h = old[hid]
        existing = {(x["date"], x["course"]): x for x in h["history"]}
        for run in history:
            e = existing.get((run["date"], run["course"]))
            if e:
                run.update({k: v for k, v in e.items() if v != ""})
        h["history"] = [x for x in history if x["date"] < "2026-10-04"]
        h["historyComplete"] = True
        h["historySource"] = "JRA公式競走馬情報・全出走レース（中央競馬）"
        h["horseCname"] = info["cname"]
        return hid, len(h["history"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for count, result in enumerate(pool.map(one, index.items()), 1):
            if count % 20 == 0 or count == len(index):
                print("Full horse history", count, "/", len(index), flush=True)
    source.write_text("const RESULT_DATA=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("Imported", len(old), "horses,", sum(len(h["history"]) for h in old.values()), "prior starts", flush=True)


def import_year_indexes():
    jobs = []
    result_source = fetch("S", "pw01skl00999999/B3", "results-index")
    params = dict(re.findall(r'objParam\["(\d+)"\]="([A-F0-9]+)"', result_source))
    current_month = re.search(r'var yearMonth = "(\d{6})"', result_source)[1]
    for year in YEARS:
        for month in range(1, int(STATS_THROUGH[5:7]) + 1 if year == YEARS[0] else 13):
            ym = f"{year}{month:02}"
            prefix = "pw01skl00" if ym >= current_month else "pw01skl10"
            jobs.append(("S", prefix + ym + "/" + params[ym[2:]], "month-" + ym))
    for role, typ, initial, prefix in [("jockey", "K", "pt01kld00999999993101/48", "pt01kld00"), ("trainer", "C", "pt02cld00999999993101/8B", "pt02cld00")]:
        source = fetch(typ, initial, role + "-leading")
        params = dict(re.findall(r'objParam\["(\d+)"\]="([A-F0-9]+)"', source))
        for year in YEARS:
            jobs.append((typ, prefix + "99" + str(year) + "99" + "3101/" + params[str(year) + "9999"], role + "-leading-" + str(year) + "-1"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for i, _ in enumerate(pool.map(lambda j: fetch(*j), jobs), 1):
            print("Year index", i, "/", len(jobs), flush=True)


def result_day_links():
    tasks = {}
    for f in CACHE.glob("month-202[456]*.html"):
        for cname in re.findall(r"'(?P<cname>pw01srl1[^']+)'", f.read_text()):
            if re.search(r"(\d{8})/", cname)[1] <= STATS_THROUGH.replace("-", ""):
                tasks[cname] = ("S", cname, "day-" + re.sub(r"\W", "", cname))
    return list(tasks.values())


def import_year_results():
    jobs = result_day_links()
    if len(jobs) < 500:
        raise ValueError("Run the year indexes import first")

    # The same all-races action changes only the fixed action prefix and its
    # additive checksum. Verify this against previously downloaded official
    # day pages before using it; fall back to reading the actual link on error.
    pairs = []
    for f in CACHE.glob("day-*.html"):
        src = re.search(r"(pw01srl1\d+)([A-F0-9]{2})$", f.stem.removeprefix("day-"))
        dst = re.search(r"'(pw01ses1\d+)/([A-F0-9]{2})'", f.read_text())
        if src and dst:
            pairs.append(dst[1] == src[1].replace("srl", "ses") and (int(dst[2], 16) - int(src[2], 16)) % 256 == 9)
    direct = len(pairs) >= 3 and all(pairs)

    def one(job):
        cn = None
        if direct:
            prefix, checksum = job[1].split("/")
            cn = prefix.replace("srl", "ses") + "/" + f"{(int(checksum, 16) + 9) % 256:02X}"
            try:
                return fetch("S", cn, "results-" + re.sub(r"\W", "", cn))
            except (ValueError, subprocess.CalledProcessError):
                pass
        text = fetch(*job)
        cn = next(iter(re.findall(r"'(pw01ses1[^']+)'", text)), None)
        if not cn:
            raise ValueError("No all-races link: " + job[2])
        return fetch("S", cn, "results-" + re.sub(r"\W", "", cn))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for i, _ in enumerate(pool.map(one, jobs), 1):
            if i % 10 == 0 or i == len(jobs):
                print("Annual race days", i, "/", len(jobs), flush=True)


def import_leading():
    jobs = [(role, typ, year) for role, typ in [("jockey", "K"), ("trainer", "C")] for year in YEARS]
    def collect(job):
        role, typ, year = job
        page = 1
        root = tree((CACHE / f"{role}-leading-{year}-1.html").read_text())
        while True:
            nxt = next((a for a in root.all("a") if a.text().startswith("次の20件")), None)
            if not nxt:
                return
            cn = re.search(r"'(pt0[12][^']+)'", str(nxt.a))[1]
            page += 1
            root = tree(fetch(typ, cn, f"{role}-leading-{year}-{page}"))
            print("Annual leading", role, year, "page", page, flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(collect, jobs))


def normalized_name(name):
    return re.sub(r"[\s☆▲△◇★]", "", unicodedata.normalize("NFKC", name))


def person_id(role, cname):
    match = re.search(r"[kc]mk0(\d{5})", cname)
    if not match:
        raise ValueError("Missing person ID: " + cname)
    return role + "-" + match[1]


def blank_year():
    return {"finishes": [0] * 6, "starts": 0, "specialWins": 0, "careerWins": None, "prize": 0, "gradeWins": {"G1": 0, "G2": 0, "G3": 0, "OP": 0, "L": 0, "JG1": 0, "JG2": 0, "JG3": 0}, "venues": {}, "courses": {}, "distances": {}, "leadingVerified": False}


def add_group(groups, key, finish):
    group = groups.setdefault(key, {"wins": 0, "starts": 0, "finishes": [0] * 6})
    group["starts"] += 1
    group["wins"] += finish == "1"
    bucket = int(finish) - 1 if finish.isdecimal() and 1 <= int(finish) <= 5 else 5
    group["finishes"][bucket] += 1


def aggregate_people():
    index = json.loads((CACHE / "index.json").read_text())["people"]
    people, by_name, leading = {}, {"jockey": {}, "trainer": {}}, {}
    for role, records in index.items():
        for name, cname in records.items():
            pid = person_id(role, cname)
            people[pid] = {"id": pid, "role": role, "name": name, "years": {str(year): blank_year() for year in YEARS}}
            by_name[role][normalized_name(name)] = pid
    for file in sorted(CACHE.glob("*-leading-202[456]-*.html")):
        role, year = re.match(r"(jockey|trainer)-leading-(202[456])-", file.name).groups()
        source = file.read_text()
        if int(year) == YEARS[0]:
            as_of = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日現在", source)
            if not as_of or "-".join([as_of[1], as_of[2].zfill(2), as_of[3].zfill(2)]) != STATS_THROUGH:
                raise ValueError("Leading snapshot date differs from result cutoff: " + file.name)
        root = tree(source)
        for row in root.first("table").first("tbody").all("tr"):
            c = cells(row)
            if len(c) != 15:
                raise ValueError("Unexpected leading columns " + file.name)
            name = normalized_name(c[1].text())
            cn = re.search(r"'(pw0[45][^']+)'", str(c[1].first("a").a))
            pid = by_name[role].get(name)
            if not pid and cn:
                pid = person_id(role, cn[1])
                by_name[role][name] = pid
                people[pid] = {"id": pid, "role": role, "name": c[1].text(), "years": {str(year): blank_year() for year in YEARS}}
            if not pid:
                continue
            def integer(node):
                return int(re.sub(r"[^0-9]", "", node.text()) or "0")
            leading[pid, year] = {"finishes": [integer(x) for x in c[2:8]], "starts": integer(c[9]), "specialWins": integer(c[8]), "careerWins": integer(c[13]), "prize": integer(c[14])}
    race_count, ride_count = {str(year): 0 for year in YEARS}, {str(year): 0 for year in YEARS}
    files = sorted(CACHE.glob("results-pw01ses1*.html"))
    expected = len(result_day_links())
    if len(files) != expected:
        raise ValueError(f"Annual results incomplete: {len(files)} / {expected}")
    for file in files:
        code, year, meeting, day, date = re.search(r"pw01ses10(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})", file.name).groups()
        venue = VENUES[int(code) - 1]
        root = tree(file.read_text())
        units = root.all("div", "race_result_unit")
        if not units:
            raise ValueError("No results on " + file.name)
        for unit in units:
            race_count[year] += 1
            course = unit.first("div", "course").text()
            distance = re.search(r"([\d,]+)メートル", course)[1].replace(",", "")
            route = re.search(r"（([^）]+)）", course)
            route = route[1].strip() if route else "障害"
            obstacle = "障害" in unit.first("div", "category").text() or "障害" in unit.first("div", "class").text() or "障害" in unit.first("span", "race_name").text() or "芝ダート" in route or "芝→" in route
            surface = "障害" if obstacle else "ダート" if "ダート" in route else "芝"
            route = "障害" if obstacle else route
            grade_images = [i.a.get("alt", "") for i in unit.first("div", "race_header").all("img") if "grade" in i.a.get("src", "")]
            grade = unicodedata.normalize("NFKC", " ".join(grade_images)).replace(" ", "").replace("・", "").replace(".", "")
            # NFKC converts the Roman numeral grade icons to GI/GII/GIII.
            grade = {"GI": "G1", "GII": "G2", "GIII": "G3", "JGI": "JG1", "JGII": "JG2", "JGIII": "JG3"}.get(grade, grade)
            grade = "L" if "リステッド" in grade else grade if grade in ["G1", "G2", "G3", "JG1", "JG2", "JG3"] else "OP" if "オープン" in unit.first("div", "class").text() else None
            for row in unit.first("tbody").all("tr"):
                cs = {c.a.get("class", ""): c for c in cells(row)}
                finish = cs.get("place", Node()).text()
                if not finish or "取消" in finish or "除外" in finish:
                    continue
                ride_count[year] += 1
                for role in ["jockey", "trainer"]:
                    name = cs.get(role, Node()).text()
                    pid = by_name[role].get(normalized_name(name))
                    if not pid:
                        # Retain people outside the displayed entry sheet too.
                        link = cs.get(role, Node()).first("a")
                        cn = re.search(r"'(pw0[45][^']+)'", str(link.a))
                        if not cn:
                            continue
                        pid = person_id(role, cn[1])
                        by_name[role][normalized_name(name)] = pid
                        people[pid] = {"id": pid, "role": role, "name": name, "years": {str(year): blank_year() for year in YEARS}}
                    annual = people[pid]["years"][year]
                    annual["starts"] += 1
                    bucket = int(finish) - 1 if finish.isdecimal() and 1 <= int(finish) <= 5 else 5
                    annual["finishes"][bucket] += 1
                    if finish == "1" and grade:
                        annual["gradeWins"][grade] += 1
                    add_group(annual["venues"], venue, finish)
                    add_group(annual["courses"], venue + " " + route + " " + distance + "m", finish)
                    add_group(annual["distances"], surface + " " + distance + "m", finish)
        if race_count[year] % 240 == 0:
            print("Aggregated", year, race_count[year], "races", flush=True)
    mismatches = []
    for pid, person in people.items():
        for year, annual in person["years"].items():
            official = leading.get((pid, year))
            if official:
                if official["finishes"] != annual["finishes"] or official["starts"] != annual["starts"]:
                    mismatches.append((person["name"], year, annual["finishes"], official["finishes"], annual["starts"], official["starts"]))
                annual.update({k: official[k] for k in ["specialWins", "careerWins", "prize"]})
                annual["leadingVerified"] = True
    if mismatches:
        (CACHE / "mismatches.json").write_text(json.dumps(mismatches, ensure_ascii=False))
        raise ValueError(f"Leading reconciliation failed: {len(mismatches)} records; see cache/mismatches.json")
    for person in people.values():
        for previous, current in zip(sorted(YEARS), sorted(YEARS)[1:]):
            older, newer = person["years"][str(previous)], person["years"][str(current)]
            if newer["careerWins"] is None and older["careerWins"] is not None:
                newer["careerWins"] = older["careerWins"] + newer["finishes"][0]
                newer["careerDerived"] = True
        for current, previous in zip(YEARS, YEARS[1:]):
            newer, older = person["years"][str(current)], person["years"][str(previous)]
            if older["careerWins"] is None and newer["careerWins"] is not None:
                older["careerWins"] = newer["careerWins"] - newer["finishes"][0]
                older["careerDerived"] = True
    data = {"fetchedAt": FETCHED_AT, "through": STATS_THROUGH, "years": YEARS, "source": "JRA公式2024・2025年全レース結果／2026年10月4日までのレース結果・リーディング情報（中央競馬のみ）", "raceCounts": race_count, "rideCounts": ride_count, "people": people}
    (ROOT / "dist/people-data.js").write_text("const PEOPLE_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
    result_file = ROOT / "dist/results-data.js"
    result = json.loads(result_file.read_text().removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    for h in result["horses"]:
        for role in ["jockey", "trainer"]:
            h[role + "Id"] = by_name[role].get(normalized_name(h.get(role, "")))
    result_file.write_text("const RESULT_DATA=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("Verified annual stats", len(people), "people;", race_count, "races;", ride_count, "starts", flush=True)


def import_history_results():
    source = ROOT / "dist/results-data.js"
    data = json.loads(source.read_text().removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    wanted = set()
    for h in data["horses"]:
        for run in h["history"]:
            if run["date"][:4] not in ["2024", "2025"]:
                wanted.add((run["date"], run["course"]))
    year_months = sorted({date[:7].replace("-", "") for date, _ in wanted})
    params = dict(re.findall(r'objParam\["(\d+)"\]="([A-F0-9]+)"', (CACHE / "results-index.html").read_text()))
    jobs = [("S", "pw01skl10" + ym + "/" + params[ym[2:]], "history-month-" + ym) for ym in year_months]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(lambda job: fetch(*job), jobs))
    days = []
    for page in pages:
        for cn in dict.fromkeys(re.findall(r"'(pw01srl10[^']+)'", page)):
            match = re.match(r"pw01srl10(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})/", cn)
            code, _, _, _, raw_date = match.groups()
            date = raw_date[:4] + "-" + raw_date[4:6] + "-" + raw_date[6:]
            if (date, VENUES[int(code)-1]) in wanted:
                prefix, checksum = cn.split("/")
                full = prefix.replace("srl", "ses") + "/" + f"{(int(checksum,16)+9)%256:02X}"
                days.append(("S", full, "history-results-" + re.sub(r"\W", "", full)))
    if len(days) != len(wanted):
        raise ValueError(f"History dates incomplete: {len(days)} / {len(wanted)}")
    print("Extra history days", len(days), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for i, _ in enumerate(pool.map(lambda job: fetch(*job), days), 1):
            if i % 30 == 0 or i == len(days):
                print("Historical race days", i, "/", len(days), flush=True)


def seconds(value):
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", value)
    return int(m[1] or 0)*60 + float(m[2]) if m else None


def enrich_full_history():
    source = ROOT / "dist/results-data.js"
    data = json.loads(source.read_text().removeprefix("const RESULT_DATA=").strip().rstrip(";"))
    wanted = {}
    for h in data["horses"]:
        for run in h["history"]:
            wanted[run["date"], run["course"], normalized_name(h["name"])] = run
    targets = {key[:2] for key in wanted}
    files = list(CACHE.glob("results-pw01ses1*.html")) + list(CACHE.glob("history-results-pw01ses1*.html"))
    enriched = set()
    for file in files:
        code, _, _, _, raw_date = re.search(r"pw01ses10(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})", file.name).groups()
        venue = VENUES[int(code)-1]
        date = raw_date[:4]+"-"+raw_date[4:6]+"-"+raw_date[6:]
        if (date,venue) not in targets:
            continue
        root = tree(file.read_text())
        for unit in root.all("div", "race_result_unit"):
            rows = unit.first("tbody").all("tr")
            first_time = next((r.first("td", "time").text() for r in rows if r.first("td", "place").text()=="1"), "")
            winner_time = seconds(first_time)
            for row in rows:
                cs = {c.a.get("class", ""): c for c in cells(row)}
                key = date, venue, normalized_name(cs.get("horse", Node()).text())
                run = wanted.get(key)
                if run is None:
                    continue
                if run["finish"] != cs.get("place", Node()).text():
                    raise ValueError("Horse history finish mismatch: " + str(key))
                timing = cs.get("time", Node()).text()
                obstacle = "障害" in unit.first("div", "category").text() or "障害" in unit.first("span", "race_name").text()
                track_text = unit.first("div", "course").text()
                surface = "障害" if obstacle else "ダート" if "ダート" in track_text else "芝"
                run.update({"name": unit.first("span", "race_name").text(), "passing": "-".join(li.text() for li in cs.get("corner", Node()).all("li")), "last3f": cs.get("f_time", Node()).text(), "weight": cs.get("h_weight", Node()).text(), "previousMargin": cs.get("margin", Node()).text(), "time": timing, "number": cs.get("num", Node()).text(), "surface": surface})
                timing_seconds = seconds(timing)
                if winner_time is not None and timing_seconds is not None:
                    run["margin"] = f"{max(0,timing_seconds-winner_time):.1f}"
                grade_img = next((i.a.get("alt", "") for i in unit.first("div", "race_header").all("img") if "grade" in i.a.get("src", "")), "")
                run["grade"] = grade_img or unit.first("div", "class").text()
                if "レコード" in run["previousMargin"]:
                    run["isRecord"] = True
                enriched.add(key)
    missing = [key for key in wanted if key not in enriched]
    if missing:
        (CACHE / "missing-history-details.json").write_text(json.dumps(missing, ensure_ascii=False))
        raise ValueError(f"Missing full-history details: {len(missing)}")
    source.write_text("const RESULT_DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("Enriched", len(enriched), "full-history records with results", flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["horses"]:
        import_horses()
    elif sys.argv[1:] == ["indexes"]:
        import_year_indexes()
    elif sys.argv[1:] == ["results"]:
        import_year_results()
    elif sys.argv[1:] == ["leading"]:
        import_leading()
    elif sys.argv[1:] == ["aggregate"]:
        aggregate_people()
    elif sys.argv[1:] == ["history-results"]:
        import_history_results()
    elif sys.argv[1:] == ["enrich-history"]:
        enrich_full_history()
    else:
        entry_index()
