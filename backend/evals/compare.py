"""Diff the two most recent eval runs: python -m evals.compare [OLD.json NEW.json]."""

import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare(old: dict, new: dict) -> list[str]:
    so, sn = old["summary"], new["summary"]
    lines = [f"old: {old['run_at']} ({old['model']})", f"new: {new['run_at']} ({new['model']})", ""]
    lines.append(f"{'category':<24}{'old':>8}{'new':>8}{'delta':>8}")
    for cat in sorted(set(so["per_category"]) | set(sn["per_category"])):
        a = so["per_category"].get(cat, {}).get("accuracy")
        b = sn["per_category"].get(cat, {}).get("accuracy")
        delta = f"{b - a:+.0%}" if a is not None and b is not None else ""
        lines.append(f"{cat:<24}{_pct(a):>8}{_pct(b):>8}{delta:>8}")
    lines.append(
        f"{'overall':<24}{_pct(so['overall_accuracy']):>8}{_pct(sn['overall_accuracy']):>8}"
        f"{sn['overall_accuracy'] - so['overall_accuracy']:>+8.0%}"
    )
    lines.append("")
    for key, label in [
        ("latency_ms_p50", "p50 latency ms"),
        ("latency_ms_p95", "p95 latency ms"),
        ("avg_tokens_per_conversation", "tokens/conversation"),
        ("avg_cost_usd_per_conversation", "$/conversation"),
        ("fabrication_failures", "fabrication failures"),
        ("no_delays_failures", "'no delays' failures"),
    ]:
        lines.append(f"{label:<24}{so.get(key)!s:>12}{sn.get(key)!s:>12}")

    old_pass = {r["id"]: r["passed"] for r in old["results"]}
    flips = []
    for r in new["results"]:
        before = old_pass.get(r["id"])
        if before is not None and before != r["passed"]:
            arrow = "FAIL -> PASS" if r["passed"] else "PASS -> FAIL"
            flips.append(f"  {arrow}  {r['id']}  {'; '.join(r['failures'])}")
    lines += ["", f"changed cases: {len(flips)}", *flips]
    return lines


def _pct(v) -> str:
    return "-" if v is None else f"{v:.0%}"


def main(argv: list[str]) -> int:
    if len(argv) == 2:
        old, new = Path(argv[0]), Path(argv[1])
    else:
        runs = sorted(RESULTS_DIR.glob("*.json"))
        if len(runs) < 2:
            print("need at least two runs in evals/results", file=sys.stderr)
            return 2
        old, new = runs[-2], runs[-1]
    print("\n".join(compare(_load(old), _load(new))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
