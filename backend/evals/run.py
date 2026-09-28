"""Run the agent eval: python -m evals.run [--category NAME] [--case ID ...] [--workers N]."""

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from app.agent.llm import AnthropicLLM
from app.config import Settings
from evals.harness import (
    SESSION_DB,
    load_cases,
    prepare_database,
    prepare_variants,
    redis_db,
    run_case,
    summarize,
)
from tests.integration.conftest import test_database_url, test_redis_url

RESULTS_DIR = Path(__file__).parent / "results"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL") or None)
    args = parser.parse_args(argv)

    cases = load_cases(args.category, args.cases)
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 2
    settings = Settings(
        database_url=test_database_url(),
        redis_url=test_redis_url(),
        **({"llm_model": args.model} if args.model else {}),
    )
    print(f"{len(cases)} cases, model {settings.llm_model}; preparing fixtures...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        engine = prepare_database(Path(tmp))
    variants = prepare_variants(settings)
    sessions = redis_db(SESSION_DB)
    sessions.flushdb()
    llm = AnthropicLLM()
    if not llm.has_credentials:
        print("ANTHROPIC_API_KEY is not set; the eval calls the real model", file=sys.stderr)
        return 2

    started = time.monotonic()

    def one(case):
        try:
            result = run_case(case, settings, engine, variants, sessions, llm)
        except Exception as exc:
            traceback.print_exc()
            result = {
                "id": case.id, "category": case.category, "setup": case.setup, "turns": [],
                "final_state": {}, "passed": False, "failures": [f"harness error: {exc!r}"],
                "fabrication_failures": 0, "no_delays_failures": 0,
            }  # fmt: skip
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"  {mark} {case.id:<28} {'; '.join(result['failures'])}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(one, cases))

    summary = summarize(results, settings.llm_model)
    summary["wall_seconds"] = round(time.monotonic() - started, 1)
    run = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "filter": {"category": args.category, "cases": args.cases},
        "summary": summary,
        "results": results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(run, indent=1), encoding="utf-8")
    print_summary(summary)
    print(f"\nwrote {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")
    return 0


def print_summary(s: dict) -> None:
    print(f"\n{'category':<24}{'pass':>6}{'total':>7}{'acc':>8}")
    for name, c in s["per_category"].items():
        print(f"{name:<24}{c['passed']:>6}{c['total']:>7}{c['accuracy']:>8.0%}")
    print(f"{'overall':<24}{s['passed']:>6}{s['cases']:>7}{s['overall_accuracy']:>8.0%}")
    print(
        f"\nfabrication failures: {s['fabrication_failures']}   "
        f"'no delays' failures: {s['no_delays_failures']}"
    )
    print(
        f"latency per turn: p50 {s['latency_ms_p50']} ms, p95 {s['latency_ms_p95']} ms "
        f"({s['turns']} turns, {s['avg_llm_calls_per_turn']} model calls/turn)"
    )
    print(
        f"per conversation: {s['avg_tokens_per_conversation']} tokens, "
        f"~${s['avg_cost_usd_per_conversation']:.4f}"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
