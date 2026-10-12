"""Extra features use only starts strictly before the forecast date."""
import math
FEATURE_NAMES = ['同馬場近5走内容','直近同馬場内容','近況変化','休養日数','同馬場近走数','直近クラス差','近走距離差','相手次走確認割合']
def enhanced_features(g, hid, race, base):
    past = list(reversed(g['past'](hid, race['date'])))
    same = [h for h in past if g['RACES'][h['raceId']]['surface'] == race['surface']][:5]
    perf = g['perf']; weighted = g['weighted']
    from datetime import date
    rest = (date.fromisoformat(race['date'])-date.fromisoformat(past[0]['date'])).days if past else 365
    newest = perf(same[0]) if same else .5
    previous = weighted(same[1:]) if len(same)>1 else .5
    pairs = [g['rivals'](h,hid,race['date']) for h in past[:5]]
    rivals = [x for rows in pairs for x in rows]
    return list(base)+[weighted(same),newest,newest-previous,math.log1p(min(rest,730))/math.log(731),len(same)/5,
        (g['RACES'][same[0]['raceId']]['grade']-race['grade'])/8 if same else 0,
        min(1,sum(abs(g['RACES'][h['raceId']]['distance']-race['distance']) for h in same)/len(same)/2000) if same else .5,
        sum(x['next'] is not None for x in rivals)/len(rivals) if rivals else 0]
def install(g):
    old = g['analyse']
    def analyse(hid,race,styles,evidence=False):
        result=old(hid,race,styles,evidence)
        if race['surface']=='障害':return result
        if evidence:
            x,ev=result
            return enhanced_features(g,hid,race,x),ev
        return enhanced_features(g,hid,race,result)
    g['analyse']=analyse
