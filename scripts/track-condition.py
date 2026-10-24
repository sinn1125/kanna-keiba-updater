"""Conservative, auditable going adjustment; coefficients are not retrained.

Uses only earlier completed starts on the same surface. Unknown is neutral.
The bounded log-odds adjustment is a rule, not a validated probability model.
"""
import math
import re
import unicodedata

VERSION = 'going-rule-v1'


def normalize_going(value, surface):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))
    text = text.replace('稍重', '稍重').replace('稍', '稍重') if '稍重' not in text else text
    if text in ('良', '稍重', '重', '不良'):
        return text
    # Do not confuse turf and dirt when both are reported in the header.
    labels = list(re.finditer(r'ダート|ダ|芝', text))
    wanted = 'ダート' if surface == 'ダート' else '芝' if surface == '芝' else None
    if wanted is None:
        return None
    for i, label in enumerate(labels):
        kind = 'ダート' if label[0] in ('ダ', 'ダート') else '芝'
        if kind == wanted:
            segment = text[label.end():labels[i+1].start() if i+1 < len(labels) else len(text)]
            found = re.search(r'不良|稍重|重|良', segment)
            if found:
                return found[0]
    return None


def adjustment(g, hid, race):
    going = normalize_going(race.get('going') or race.get('weather'), race['surface'])
    evidence = {'version': VERSION, 'condition': going, 'starts': 0,
                'baselineStarts': 0, 'logOddsAdjustment': 0.0,
                'status': 'unknown-condition', 'examples': [], 'validated': False}
    if going is None or race['surface'] == '障害':
        return evidence
    rows = []
    for h in reversed(g['past'](hid, race['date'])):
        previous = g['RACES'][h['raceId']]
        if h['date'] >= race['date'] or previous['surface'] != race['surface'] or not h.get('finish'):
            continue
        condition = normalize_going(previous.get('going') or previous.get('weather'), previous['surface'])
        if condition:
            rows.append((h, condition))
    same = [h for h, condition in rows if condition == going]
    other = [h for h, condition in rows if condition != going]
    evidence.update(starts=len(same), baselineStarts=len(other), status='insufficient-history')
    evidence['examples'] = [{'date':h['date'], 'raceId':h['raceId'], 'finish':h['finish']} for h in same[:5]]
    # A contrast requires evidence in both conditions; never reward experience alone.
    if len(same) < 2 or len(other) < 2:
        return evidence
    contrast = g['weighted'](same) - g['weighted'](other)
    confidence = len(same)/(len(same)+5) * len(other)/(len(other)+5)
    evidence.update(status='applied', logOddsAdjustment=max(-.25, min(.25, contrast*confidence)))
    return evidence


def apply_probability(probability, evidence):
    delta = evidence['logOddsAdjustment']
    if probability is None or not delta:
        return probability
    p = max(1e-12, min(1-1e-12, probability))
    return 1/(1+math.exp(-(math.log(p/(1-p))+delta)))


def before_start(race, now):
    start = race.get('startTime', '')
    return (race.get('status') == 'entries' and bool(re.fullmatch(r'\d{2}:\d{2}', start))
            and now.strftime('%Y-%m-%d %H:%M') < race['date']+' '+start)
