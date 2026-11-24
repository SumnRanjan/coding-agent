"""
OBSERVABILITY: a tiny tracing system.

Every user request = one TRACE. Inside it we record EVENTS:
    llm_call   -> input/output tokens, latency, estimated cost, which tools the LLM asked for
    tool_call  -> tool name, arguments, result, latency, error / blocked
    guardrail  -> what was blocked and why
    verify     -> compile result, program output

Each trace is saved as JSON in traces/ so you can debug any run later.
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

# USD per 1M tokens (input, output). Check https://groq.com/pricing and update if it changes.
PRICES = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}

USD_TO_INR = 88  # rough, only for display


class Tracer:
    def __init__(self, name: str, model: str, trace_dir: Path | str = "traces", save: bool = True):
        self.trace_id = uuid.uuid4().hex[:8]
        self.name = name
        self.model = model
        self.trace_dir = Path(trace_dir)
        self.save_enabled = save
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._t0 = time.perf_counter()
        self.events: list[dict] = []

    # ---------------- recording ----------------

    def _add(self, kind: str, **data) -> dict:
        event = {"type": kind, "t": round(time.perf_counter() - self._t0, 3), **data}
        self.events.append(event)
        return event

    def llm_call(self, usage, latency: float, tool_calls: list[str], error: str | None = None):
        prompt = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion = getattr(usage, "completion_tokens", 0) if usage else 0
        self._add(
            "llm_call",
            model=self.model,
            input_tokens=prompt,
            output_tokens=completion,
            latency_s=round(latency, 3),
            cost_usd=self.cost(prompt, completion),
            tool_calls=tool_calls,
            error=error,
        )

    def tool_call(self, name: str, args: dict, result: str, latency: float,
                  error: str | None = None, blocked: str | None = None):
        self._add(
            "tool_call",
            tool=name,
            args=args,
            result=result[:2000],  # keep trace files small
            latency_s=round(latency, 4),
            error=error,
            blocked=blocked,
        )

    def guardrail(self, stage: str, reason: str, **extra):
        self._add("guardrail", stage=stage, reason=reason, **extra)

    def event(self, kind: str, **data):
        self._add(kind, **data)

    # ---------------- metrics ----------------

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = PRICES.get(self.model, (0.0, 0.0))
        return round(input_tokens / 1e6 * p_in + output_tokens / 1e6 * p_out, 6)

    def summary(self) -> dict:
        llm = [e for e in self.events if e["type"] == "llm_call"]
        tools = [e for e in self.events if e["type"] == "tool_call"]
        return {
            "llm_calls": len(llm),
            "input_tokens": sum(e["input_tokens"] for e in llm),
            "output_tokens": sum(e["output_tokens"] for e in llm),
            "llm_latency_s": round(sum(e["latency_s"] for e in llm), 2),
            "total_latency_s": round(time.perf_counter() - self._t0, 2),
            "cost_usd": round(sum(e["cost_usd"] for e in llm), 6),
            "tool_calls": len(tools),
            "tool_errors": sum(1 for e in tools if e["error"]),
            "guardrail_blocks": sum(1 for e in self.events if e["type"] == "guardrail"),
        }

    # ---------------- output ----------------

    def save(self, **final) -> Path | None:
        if not self.save_enabled:
            return None
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.replace(":", "-")
        path = self.trace_dir / f"{stamp}_{self.trace_id}.json"
        data = {
            "trace_id": self.trace_id,
            "name": self.name,
            "model": self.model,
            "started_at": self.started_at,
            "summary": self.summary(),
            "final": final,
            "events": self.events,
        }
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path

    def print_summary(self):
        s = self.summary()
        print("\n" + "-" * 60)
        print(f"TRACE {self.trace_id}  ({self.name})")
        print("-" * 60)
        print(f"LLM calls      : {s['llm_calls']}")
        print(f"Tokens         : {s['input_tokens']} in / {s['output_tokens']} out")
        print(f"Latency        : {s['llm_latency_s']}s in LLM, {s['total_latency_s']}s total")
        print(f"Estimated cost : ${s['cost_usd']:.5f}  (~Rs {s['cost_usd'] * USD_TO_INR:.3f})")
        print(f"Tool calls     : {s['tool_calls']}  (errors: {s['tool_errors']})")
        print(f"Guardrail hits : {s['guardrail_blocks']}")
