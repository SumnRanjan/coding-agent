"""
Mini Coding Agent -- solve simple C++ bugs with an LLM + tools.

Now with:
    OBSERVABILITY -> every run is traced (tokens, latency, cost, tool args/results) in traces/
    GUARDRAILS    -> input checks, tool checks, secret redaction (guardrails.py)
    EVALS         -> run `uv run evals/run_evals.py` to score the agent on a golden dataset

Run:
    uv run main.py                 # works on cpp_code/
    uv run main.py some/other/dir  # works on another folder
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq  # type: ignore

from agent import MODEL, run_agent
from tools import code_files
from tracing import Tracer

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent


def ask(question: str) -> str:
    try:
        return input(question).strip()
    except EOFError:
        return ""


def human_verify(compiled: bool, output: str) -> tuple[bool, str]:
    """Human-in-the-loop check: you decide if the bug is fixed."""
    if ask("\nIs the bug fixed? (y/n): ").lower().startswith("y"):
        return True, ""
    return False, ask("What is still wrong? (Enter to skip): ")


def main():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in .env")
    client = Groq(api_key=api_key, max_retries=10)

    workspace = (Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cpp_code").resolve()
    files = code_files(workspace)

    print("\n" + "=" * 60 + "\nMINI CODING AGENT\n" + "=" * 60)
    print(f"Workspace: {workspace}")
    print(f"Files    : {', '.join(f.name for f in files) or '(none)'}")
    if not files:
        return

    bug = ask("\nDescribe the bug in simple English: ")
    tracer = Tracer(name="interactive", model=MODEL, trace_dir=ROOT / "traces")

    result = run_agent(client, workspace, bug, human_verify, tracer)

    print("\n" + "=" * 60)
    print(f"RESULT: {result['status'].upper()}  (attempts: {result['attempts']})")
    if result["diff"]:
        print("\nChanges made:\n" + result["diff"])

    tracer.print_summary()
    print(f"Trace saved: {result.get('trace')}")


if __name__ == "__main__":
    main()
