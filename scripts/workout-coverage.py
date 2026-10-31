"""Conservative missing-history fallback and workout-source coverage.

No source record means unrecorded, not that the horse did not train.
Uniform priors are explicitly untrained and must never drive betting advice.
"""

def workout_status(horse_ids, records):
    horses = list(dict.fromkeys(horse_ids))
    statuses = {}
    for horse in horses:
        record = records.get(horse)
        if record is None:
            statuses[horse] = 'unrecorded'
        elif record.get('status') == 'confirmed' and record.get('sessions'):
            statuses[horse] = 'confirmed'
        elif record.get('status') in ('not_entered', 'not_published', 'fetch_failed', 'partial', 'invalid'):
            statuses[horse] = record['status']
        else:
            statuses[horse] = 'unverified'
    count = sum(s == 'confirmed' for s in statuses.values())
    coverage = 'unknown' if not records else ('full' if count == len(horses) else 'partial' if count else 'none_confirmed')
    return {'coverage': coverage, 'confirmed': count, 'total': len(horses), 'horses': statuses}


def fallback_scores(items, active_ids):
    """Add explicitly provisional priors; never overwrite an observed model score."""
    active_ids = list(dict.fromkeys(active_ids))
    n = len(active_ids)
    if n < 2:
        return items, {'mode': 'insufficient', 'missing': n, 'total': n}
    output = {k: dict(v) for k, v in items.items()}
    missing = [hid for hid in active_ids if output[hid].get('winScore') is None]
    for hid in missing:
        output[hid]['winScore'] = 1 / n
        output[hid]['placeScore'] = min(1, 2 / n)
        output[hid]['podiumScore'] = min(1, 3 / n)
        output[hid]['scoreOrigin'] = 'untrained_uniform_prior'
        output[hid]['confidence'] = 'low'
    return output, {'mode': 'provisional' if missing else 'original', 'missing': len(missing), 'total': n,
                    'warning': 'Untrained prior, not a validated model' if missing else None}
