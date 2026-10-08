"""Check a newly imported NAR day before publishing it."""
import csv
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
day = sys.argv[1] if len(sys.argv) > 1 else None
assert day and len(day) == 10 and day[4] == '-' and day[7] == '-', 'Pass YYYY-MM-DD'
data = json.loads((root / 'dist/local-data.js').read_text().split('=', 1)[1].rstrip(';\n'))
odds = json.loads((root / 'dist/local-odds-data.js').read_text().split('Object.assign(ODDS_DATA,', 1)[1].rstrip(');\n'))
source_file = root.parent / 'nar-cache' / (day.replace('-', '') + '_racelist.csv')
with source_file.open(encoding='utf-8-sig') as file:
    official = list(csv.DictReader(file))
with (source_file.parent / (day.replace('-', '') + '_odds.csv')).open(encoding='utf-8-sig') as file:
    official_odds = list(csv.DictReader(file))
for suffix in ('racelist', 'horselist', 'payback', 'odds'):
    with (source_file.parent / (day.replace('-', '') + '_' + suffix + '.csv')).open(encoding='utf-8-sig') as file:
        records = list(csv.DictReader(file))
    assert all(x['競走年月日'] == day.replace('-', '') for x in records), ('Wrong CSV date', suffix)
races = [r for r in data['races'] if r['date'] == day]
assert data['snapshotDate'] == day
assert official and len(races) == len(official), (len(races), len(official))
assert len({r['id'] for r in data['races']}) == len(data['races'])
assert len({h['id'] for h in data['horses']}) == len(data['horses'])
for r in races:
    horses = [h for h in data['horses'] if h['raceId'] == r['id']]
    assert horses and len(horses) == r['field'], (r['id'], len(horses), r['field'])
    assert r['status'] in ('entries', 'results')
    if r['status'] == 'entries':
        assert not r['payouts'] and not any(h['finish'].isdigit() for h in horses)
    source_has_odds = any(x['競馬場'] == r['course'] and int(x['レース番号']) == r['number'] for x in official_odds)
    if source_has_odds:
        assert r['id'] in odds, ('Published odds missing', r['id'])
    else:
        assert not odds.get(r['id']), ('Unpublished odds populated', r['id'])
print(f"VERIFIED {day}: {len(races)} races, {sum(h['raceId'] in {r['id'] for r in races} for h in data['horses'])} horses")

print("STATUS", {status: sum(r["status"] == status for r in races) for status in ("entries", "results")})
