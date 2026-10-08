"""Update official public data; failed organization updates never replace snapshots."""
import concurrent.futures
import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
DATE = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()

def parse(path):
    text = path.read_text()
    return json.loads(text[text.index('{'):text.rindex('}') + 1])

def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, timeout=480)

def verify(path):
    data = parse(path)
    races, horses = data['races'], data['horses']
    ids = {r['id'] for r in races}
    assert len(ids) == len(races), 'Duplicate race ID'
    assert len({h['id'] for h in horses}) == len(horses), 'Duplicate horse ID'
    assert all(h['raceId'] in ids for h in horses), 'Unknown race'
    for r in races:
        datetime.date.fromisoformat(r['date'])
        assert r['status'] in ('entries', 'results')
        hs = [h for h in horses if h['raceId'] == r['id']]
        assert len(hs) == int(r['field']), ('Incomplete field', r['id'])
        if r['status'] == 'entries':
            assert not r.get('payouts')
            assert not any(str(h.get('finish', '')).isdigit() for h in hs)

def update(kind):
    names = ['results-data.js'] if kind == 'jra' else ['local-data.js', 'local-odds-data.js']
    old = {name: (DIST / name).read_bytes() for name in names}
    try:
        if kind == 'jra':
            run('scripts/import-jra-entries.py')
            run('scripts/import-jra-results.py', DATE)
            verify(DIST / 'results-data.js')
        else:
            run('scripts/import-local.py', DATE, '--quick')
            run('scripts/verify-local-snapshot.py', DATE)
            verify(DIST / 'local-data.js')
        return {'organization': kind, 'success': True, 'changed': any(old[n] != (DIST/n).read_bytes() for n in names)}
    except Exception as error:
        for name, content in old.items():
            (DIST / name).write_bytes(content)
        return {'organization': kind, 'success': False, 'error': str(error)}

def main():
    DIST.mkdir(exist_ok=True)
    # Bootstrap an independent official feed. Existing app/browser records stay
    # in the app; no authentication or Site scraping is required by the runner.
    seeds = {
        'results-data.js': 'const RESULT_DATA={"races":[],"horses":[]};\n',
        'local-data.js': 'const LOCAL_DATA=' + json.dumps({'year': int(DATE[:4]), 'snapshotDate': '', 'fetchedAt': '', 'source': '', 'schedule': [], 'races': [], 'horses': [], 'errors': []}) + ';\n',
        'local-odds-data.js': 'Object.assign(ODDS_DATA,{});\n',
    }
    for name, content in seeds.items():
        path = DIST / name
        if not path.exists():
            path.write_text(content)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ('jra',)))
    feed = {'version': 1, 'checkedAt': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds'), 'date': DATE, 'results': results,
            'jra': parse(DIST / 'results-data.js'), 'nar': {'races': [], 'horses': [], 'schedule': []}, 'narOdds': {}}
    (DIST / 'feed.json').write_text(json.dumps(feed, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(json.dumps({'date': DATE, 'results': results}, ensure_ascii=False), flush=True)
    if any(not r['success'] for r in results):
        sys.exit(1)

if __name__ == '__main__':
    main()
