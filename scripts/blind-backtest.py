"""Blind evaluation of immutable pre-race JRA forecasts.

Input JSON: {"races":[{"race_id":"...", "start_at":"ISO offset",
"forecast_created_at":"ISO offset", "forecast_kind":"pre-race",
"prediction":{"horse-id": {"win_probability":0.2, "mark":"◎"}},
"result":{"winner_id":"horse-id"}}]}

The runner passed to predict() never receives result. Existing saved forecasts
can be scored without a predictor; this does NOT simulate workout ablations.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path


def timestamp(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("Timezone required")
    return d


def blind_evaluate(races, predictor=None):
    accepted, rejected = [], []
    for race in races:
        rid = race.get("race_id", "unknown")
        try:
            if race.get("forecast_kind") != "pre-race":
                raise ValueError("not a saved pre-race forecast")
            if timestamp(race["forecast_created_at"]) >= timestamp(race["start_at"]):
                raise ValueError("forecast was not saved before start")
            # Never pass the result, finish, payouts, or post-start odds to the predictor.
            inputs = {k: race[k] for k in ("race_id", "start_at", "entrants", "pre_race_features", "workout_data") if k in race}
            prediction = predictor(inputs) if predictor else race["prediction"]
            winner = race["result"]["winner_id"]
            if winner not in prediction:
                raise ValueError("winner absent from predicted field")
            probs = [v["win_probability"] for v in prediction.values()]
            if not probs or any(not isinstance(p, (float, int)) or isinstance(p, bool)
                                or not 0 <= p <= 1 for p in probs):
                raise ValueError("invalid probabilities")
            if abs(sum(probs) - 1) > 0.0001:
                raise ValueError("win probabilities do not sum to 1")
            if set(race["result"].get("runner_ids", prediction)) != set(prediction):
                raise ValueError("runner mismatch")
            top = max(prediction, key=lambda h: prediction[h]["win_probability"])
            # Multiclass Brier: lower is better; 0 is perfect.
            brier = sum((v["win_probability"] - int(h == winner)) ** 2
                        for h, v in prediction.items())
            accepted.append({"race_id": rid, "top1_hit": top == winner, "brier": brier})
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({"race_id": rid, "reason": str(exc)})
    n = len(accepted)
    return {"evaluated": n, "rejected": len(rejected),
            "top1_accuracy": sum(x["top1_hit"] for x in accepted) / n if n else None,
            "mean_multiclass_brier": sum(x["brier"] for x in accepted) / n if n else None,
            "excluded": rejected}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Saved pre-race forecast + sealed results JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = blind_evaluate(json.loads(args.input.read_text(encoding="utf-8"))["races"])
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
