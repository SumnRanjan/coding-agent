"""
EVALS: score the coding agent on a golden dataset.

For each test case:
    1. copy the buggy C++ into a temp folder (originals are never touched)
    2. run the agent automatically (program output is checked instead of asking a human)
    3. DETERMINISTIC checks  -> objective pass/fail
         - blocked when it should be (guardrail cases)
         - expected text in the program output
         - number of edits <= max_edits
         - read code before editing (trajectory check)
         - no forbidden code in the diff
    4. LLM-AS-A-JUDGE        -> subjective quality (1-5): minimal fix? explanation matches the change?
    5. report: pass rate, attempts, tokens, cost, latency + results saved to evals/results/

Run (from the coding_agent folder):
    uv run evals/run_evals.py              # all cases, with LLM judge
    uv run evals/run_evals.py --no-judge   # deterministic checks only (cheaper)
    uv run evals/run_evals.py --only factorial_loop_bound
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from groq import Groq  # type: ignore  # noqa: E402

from agent import MODEL, run_agent  # noqa: E402
from tracing import Tracer  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
JUDGE_MODEL = MODEL

JUDGE_PROMPT = """You are grading an AI coding agent that fixes small C++ bugs.

Bug report:
{bug}

Reference fix (what a good fix looks like):
{expected_fix}

The agent's change (unified diff):
{diff}

The agent's explanation:
{summary}

Grade the fix. Reply with JSON only:
{{
  "score": <1-5, 5 = correct, minimal, well explained>,
  "minimal_change": <true if only the buggy code was changed>,
  "explanation_matches_change": <true if the explanation describes the actual diff>,
  "reason": "<one sentence>"
}}"""


# ============================================================
# DETERMINISTIC CHECKS
# ============================================================

def deterministic_checks(case: dict, result: dict) -> dict:
    checks = {}

    if case.get("expect_blocked"):
        checks["blocked_by_guardrail"] = result["status"] == "blocked"
        return checks

    checks["not_blocked"] = result["status"] != "blocked"
    checks["fixed"] = result["status"] == "fixed"
    checks["expected_output"] = all(s in result["output"] for s in case.get("expected_output_contains", []))

    edits = [t for t in result["trajectory"] if t["tool"] == "replace_line" and not t["error"] and not t["blocked"]]
    checks["edits_within_limit"] = 0 < len(edits) <= case.get("max_edits", 1)

    # trajectory: the agent must read the code before its first edit
    tools = [t["tool"] for t in result["trajectory"]]
    first_edit = tools.index("replace_line") if "replace_line" in tools else None
    checks["read_before_edit"] = first_edit is not None and "search_in_file" in tools[:first_edit]

    forbidden = case.get("forbidden_in_diff", [])
    added = "\n".join(l for l in result["diff"].splitlines() if l.startswith("+"))
    checks["no_forbidden_code"] = not any(f in added for f in forbidden)

    return checks


# ============================================================
# LLM-AS-A-JUDGE
# ============================================================

def llm_judge(client, case: dict, result: dict, tracer: Tracer) -> dict:
    if case.get("expect_blocked") or not result["diff"]:
        return {}
    prompt = JUDGE_PROMPT.format(
        bug=case["bug"],
        expected_fix=case.get("expected_fix", "(none)"),
        diff=result["diff"][:3000],
        summary=result["summary"][:1000],
    )
    import time
    t0 = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        tracer.llm_call(response.usage, time.perf_counter() - t0, [])
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"score": None, "reason": f"judge failed: {e}"}


# ============================================================
# RUN ONE CASE
# ============================================================

def run_case(client, case: dict, use_judge: bool) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"eval_{case['id']}_"))
    shutil.copytree(EVAL_DIR / "cases" / case["case_dir"], tmp, dirs_exist_ok=True)

    expected = case.get("expected_output_contains", [])

    def auto_verify(compiled: bool, output: str) -> tuple[bool, str]:
        missing = [s for s in expected if s not in output]
        if not missing:
            return True, ""
        return False, f"Expected the output to contain: {missing}"

    tracer = Tracer(name=f"eval:{case['id']}", model=MODEL, trace_dir=EVAL_DIR / "traces")
    result = run_agent(client, tmp, case["bug"], auto_verify, tracer, log=lambda *a, **k: None)

    checks = deterministic_checks(case, result)
    judge = llm_judge(client, case, result, tracer) if use_judge else {}
    shutil.rmtree(tmp, ignore_errors=True)

    return {
        "id": case["id"],
        "category": case["category"],
        "passed": all(checks.values()),
        "checks": checks,
        "judge": judge,
        "status": result["status"],
        "attempts": result["attempts"],
        "diff": result["diff"],
        "summary": result["summary"],
        "metrics": tracer.summary(),
        "trace": result.get("trace"),
    }


# ============================================================
# REPORT
# ============================================================

def print_report(rows: list[dict]):
    print("\n" + "=" * 90)
    print(f"{'CASE':32} {'CAT':10} {'PASS':5} {'ATT':4} {'JUDGE':6} {'TOKENS':8} {'COST $':9} {'SEC':6}")
    print("-" * 90)
    for r in rows:
        m = r["metrics"]
        score = r["judge"].get("score") if r["judge"] else "-"
        print(f"{r['id'][:32]:32} {r['category']:10} {'✅' if r['passed'] else '❌':5} "
              f"{r['attempts']:<4} {str(score):6} {m['input_tokens'] + m['output_tokens']:<8} "
              f"{m['cost_usd']:<9.5f} {m['total_latency_s']:<6}")
        if not r["passed"]:
            failed = [k for k, v in r["checks"].items() if not v]
            print(f"{'':32} failed checks: {', '.join(failed)}")

    n = len(rows)
    passed = sum(r["passed"] for r in rows)
    fixes = [r for r in rows if r["category"] != "guardrail"]
    scores = [r["judge"]["score"] for r in rows if r["judge"].get("score") is not None]
    total_cost = sum(r["metrics"]["cost_usd"] for r in rows)
    total_tokens = sum(r["metrics"]["input_tokens"] + r["metrics"]["output_tokens"] for r in rows)

    print("=" * 90)
    print(f"Pass rate          : {passed}/{n}  ({passed / n:.0%})")
    if fixes:
        print(f"Avg attempts (fix) : {sum(r['attempts'] for r in fixes) / len(fixes):.2f}")
    if scores:
        print(f"Avg judge score    : {sum(scores) / len(scores):.2f} / 5")
    print(f"Total tokens       : {total_tokens}")
    print(f"Total cost         : ${total_cost:.5f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-judge", action="store_true", help="skip LLM-as-a-judge")
    parser.add_argument("--only", help="run a single case id")
    args = parser.parse_args()

    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in .env")
    client = Groq(api_key=api_key, max_retries=10)

    cases = json.loads((EVAL_DIR / "golden_dataset.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]

    rows = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ...", flush=True)
        rows.append(run_case(client, case, use_judge=not args.no_judge))

    print_report(rows)

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"eval_{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(f"\nResults saved: {out}")


if __name__ == "__main__":
    main()
