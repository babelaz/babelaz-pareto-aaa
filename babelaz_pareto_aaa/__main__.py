"""Command line for the portable core (no Hermes).

  python -m babelaz_pareto_aaa qualify --call-model module:function [--out DIR]

qualify: the anchor test for one model. module:function is your own call_model(images, text) -> (text, usage, model_id).
(The /goal gate fallback, `gate`, was removed after M3 review 1: stamped STATUS passed day-1 test 2.)
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

from . import check, judge


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="babelaz_pareto_aaa")
    p.add_argument("cmd", choices=["qualify"])
    p.add_argument("--call-model", required=True, help="module:function")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    mod, _, fn = a.call_model.partition(":")
    call = getattr(importlib.import_module(mod), fn)
    results = {}
    for preset in check.PRESETS:
        dated, _clean, current, _ = check.anchors(preset)
        results[preset] = judge.qualify(call, dated, current)
    for preset, r in results.items():
        s = r["scores"]
        print(f"{preset}: {'PASS' if r['passed'] else 'FAIL'} · dated {s.get('dated')} · current {s.get('current')} · {r['reason']} · model {r['model']}")
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "qualify.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 0 if all(r["passed"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
