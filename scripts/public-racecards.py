"""Publish the minimal read-only race card payload from a verified JRA feed."""
import json
from pathlib import Path

RACE_FIELDS = ('id', 'date', 'course', 'number', 'name', 'distance', 'track', 'startTime', 'field', 'status')
HORSE_FIELDS = ('id', 'raceId', 'name', 'number', 'frame', 'jockey', 'trainer', 'load', 'age', 'weight', 'weightChange', 'entryState')

def racecards(feed):
    assert feed.get('version') == 1 and isinstance(feed.get('jra'), dict)
    races = [{k: r[k] for k in RACE_FIELDS if k in r} for r in feed['jra']['races']]
    horses = [{k: h[k] for k in HORSE_FIELDS if k in h} for h in feed['jra']['horses']]
    race_ids = {r['id'] for r in races}
    horse_ids = {h['id'] for h in horses}
    assert len(race_ids) == len(races) and len(horse_ids) == len(horses)
    for r in races:
        assert r['id'].startswith('jra-') and r['status'] in ('entries', 'results')
        assert sum(h['raceId'] == r['id'] and h.get('entryState') != 'not_listed' for h in horses) == int(r['field'])
    assert all(h['raceId'] in race_ids for h in horses)
    return {'version': 1, 'checkedAt': feed['checkedAt'], 'jra': {'races': races, 'horses': horses}}

def write_public_racecards(feed, path):
    public = racecards(feed)
    path.write_text(json.dumps(public, ensure_ascii=False, separators=(',', ':')) + '\n')

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    write_public_racecards(json.loads((root / 'dist/feed.json').read_text()), root / 'dist/public-racecards.json')
