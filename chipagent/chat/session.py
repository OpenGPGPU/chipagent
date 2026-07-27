"""Conversational agent layer (Phase 2): ChatSession + LLM-driven intent.

This is the layer that makes ChipAgent feel like Claude / OpenClaw rather than
a batch compiler. A :class:`ChatSession` holds context across turns (the
current design spec, the last selected design + Pareto front + tradeoff
table, conversation history) and routes each user message through a
two-step LLM loop:

1. **Intent** — the LLM picks one action from a small catalogue (run DSE,
   refine the last run with new targets, explain the selection, show a
   candidate's RTL/driver, or plain chat). Robust: a rule-based router
   backs this up when the LLM is offline or emits unparseable output.
2. **Reply** — the LLM explains the action's result in natural language
   (with a compact tradeoff table when relevant), streamed token-by-token
   via :meth:`LLMClient.stream`.

The DSE loop and task panel do the real work; this layer only adds the
conversational surface. It is also what the MCP server (``chipagent.mcp``)
exposes to Claude Code — Claude Code is then *another* conversational front
over the same backend.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from ..config import Settings
from ..llm import LLMClient, LLMError


# ----------------------------------------------------------------------
# Action catalogue the LLM can pick from. Kept tiny and unambiguous so a
# small/fast model returns valid JSON.
# ----------------------------------------------------------------------
_ACTIONS_SCHEMA = """\
Available actions (reply with ONLY one JSON object):
- {"action":"dse","request":"<the design request in the user's words>","targets":{"area":<num>,"fmax":<num>,"latency":<num>,"power":<num>,"sw_cost":<num>}}  — run the HW/SW co-design DSE loop. Only set targets the user mentioned.
- {"action":"refine","targets":{...}}  — re-run the LAST design with adjusted targets (user said "smaller"/"faster"/"lower power").
- {"action":"explain"}  — explain why the current selected design was chosen.
- {"action":"show","variant_id":"vN","what":"rtl|driver|tradeoff"}  — show a specific candidate's artifact.
- {"action":"chat"}  — general reply (greetings, capability questions, anything not a design action).
If unsure, pick "chat". Never invent variant_ids not in the context."""

_SYSTEM_ACTION = (
    "You are ChipAgent's intent router. Read the user message and the session "
    "context, then choose exactly one action. If the user describes a module or "
    "feature to design (RTL/DMA/UART/AXI/etc., with or without numeric targets), "
    "pick \"dse\". Only pick \"chat\" for greetings or capability questions that "
    "are NOT design requests. " + _ACTIONS_SCHEMA
)

_SYSTEM_REPLY = (
    "You are ChipAgent, a chip HW/SW co-design agent. You CAN run design-space "
    "exploration, generate RTL / register header / HAL / driver / testbench, run "
    "simulation, and check register alignment — these ran as the action you just "
    "took. Never say you cannot do these. You are explaining the result of that "
    "action to the user, in concise natural language (Chinese if the user writes "
    "Chinese, English otherwise). When a tradeoff table is provided, render it as "
    "a compact markdown table and point out which row you picked and why. Be "
    "specific about area / Fmax / latency / power / software cost and which "
    "target is violated, if any. Do not fabricate numbers — use exactly the ones "
    "in the result."
)


@dataclass
class ChatSession:
    """Holds conversation + design context; routes one user turn."""

    llm: Optional[LLMClient] = None
    settings: Settings = field(default_factory=Settings.load)
    history: List[Dict[str, str]] = field(default_factory=list)
    last_result: Dict[str, Any] = field(default_factory=dict)  # last DSE result

    # ------------------------------------------------------------------
    # Public: handle one user turn, streaming the prose reply.
    # ------------------------------------------------------------------
    def handle(self, user_text: str) -> Iterator[str]:
        """Yield prose-reply deltas for this turn (streaming)."""
        self.history.append({"role": "user", "content": user_text})
        action = self._decide_action(user_text)
        result, action_summary = self._execute(action, user_text)
        # Stream the explanation. The reply generator gets the result + the
        # original user message + the action it took.
        yield from self._stream_reply(user_text, action_summary, result)
        # Keep a record for multi-turn context (non-streaming, truncated).
        self.history.append({"role": "assistant", "content": action_summary})
        if action.get("action") in ("dse", "refine"):
            self.last_result = result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # Step 1: intent — LLM picks an action, rule-based fallback.
    # ------------------------------------------------------------------
    def _decide_action(self, user_text: str) -> Dict[str, Any]:
        rule = self._rule_action(user_text)  # always computed as a safety net
        if self.llm is None or not self.llm.available:
            return rule
        context = self._context_block()
        user = (f"Session context:\n{context}\n\n"
                f"Conversation so far (last 4 messages):\n{self._recent()}\n\n"
                f"User message: {user_text}\n\nChoose one action. JSON only.")
        try:
            raw = self.llm.chat(_SYSTEM_ACTION, user, temperature=0.0, max_tokens=512)
            action = LLMClient.extract_json(raw)
            if action and "action" in action:
                # Safety net: if the LLM picked "chat" but the message is clearly
                # a design request or a refinement, honour the rule router so a
                # real design action actually runs (LLM-primary, rule-guarded).
                if action["action"] == "chat" and rule["action"] in ("dse", "refine"):
                    return rule
                return action
        except LLMError:
            pass
        return rule

    def _rule_action(self, user_text: str) -> Dict[str, Any]:
        """Deterministic fallback: keyword → action."""
        t = user_text.lower()
        # Refinement of the last run.
        if self.last_result and any(k in user_text for k in
                                    ("再小", "再快", "更低功耗", "更小", "更快",
                                     "smaller", "faster", "lower power", "tighter")):
            targets = self._extract_rule_targets(user_text)
            return {"action": "refine", "targets": targets}
        # Explain the current selection.
        if any(k in user_text for k in ("为什么", "why", "理由", "explain")):
            return {"action": "explain"}
        # Show a candidate.
        import re
        m = re.search(r"\bv(\d+)\b", user_text)
        if m and any(k in user_text for k in ("rtl", "driver", "看", "show", "展示")):
            what = "driver" if "driver" in t else "rtl"
            return {"action": "show", "variant_id": f"v{m.group(1)}", "what": what}
        # A fresh design request — run DSE.
        if any(k in user_text for k in ("生成", "设计", "generate", "design", "模块", "dma", "axi", "uart")):
            targets = self._extract_rule_targets(user_text)
            return {"action": "dse", "request": user_text, "targets": targets}
        return {"action": "chat"}

    @staticmethod
    def _extract_rule_targets(user_text: str) -> Dict[str, Any]:
        import re
        out: Dict[str, Any] = {}
        a = re.search(r"面积[^\d]{0,6}(\d+)|area[^\d]{0,6}(\d+)", user_text, re.I)
        if a:
            out["area"] = int(next(g for g in a.groups() if g))
        f = re.search(r"(\d+)\s*mhz|fmax[^\d]{0,6}(\d+)|(\d+)\s*兆", user_text, re.I)
        if f:
            out["fmax"] = int(next(g for g in f.groups() if g))
        l = re.search(r"延迟[^\d]{0,6}(\d+)|latency[^\d]{0,6}(\d+)", user_text, re.I)
        if l:
            out["latency"] = int(next(g for g in l.groups() if g))
        p = re.search(r"功耗[^\d]{0,6}(\d+)|power[^\d]{0,6}(\d+)", user_text, re.I)
        if p:
            out["power"] = int(next(g for g in p.groups() if g))
        return out

    # ------------------------------------------------------------------
    # Step 2: execute the action — thin calls into chipagent's backend.
    # ------------------------------------------------------------------
    def _execute(self, action: Dict[str, Any], user_text: str):
        kind = action.get("action", "chat")
        if kind == "dse":
            return self._do_dse(action.get("request", user_text),
                                action.get("targets") or {})
        if kind == "refine":
            return self._do_refine(action.get("targets") or {})
        if kind == "explain":
            return self._do_explain()
        if kind == "show":
            return self._do_show(action.get("variant_id"), action.get("what", "rtl"))
        return self._do_chat(user_text)

    def _do_dse(self, request: str, targets: Dict[str, Any]):
        from ..dse import run_dse
        r = run_dse(request, targets=targets or None, use_llm=False)
        return r, f"ran DSE: {request} targets={targets} → selected {r.get('selected',{}).get('variant_id')}"

    def _do_refine(self, targets: Dict[str, Any]):
        from ..dse import run_dse
        prev = self.last_result or {}
        spec = prev.get("spec") or {}
        request = (spec.get("module_name") + " " + (spec.get("description") or "")).strip()
        if not request:
            return {"error": "no previous design to refine"}, "no previous design"
        # Merge new targets onto the previous spec's targets.
        r = run_dse(request, targets=targets or None, use_llm=False)
        return r, f"refined last design with targets={targets} → selected {r.get('selected',{}).get('variant_id')}"

    def _do_explain(self):
        if not self.last_result:
            return {"error": "no design yet"}, "nothing to explain"
        sel = self.last_result.get("selected", {}) or {}
        return {
            "selected": sel,
            "tradeoff_table": self.last_result.get("tradeoff_table"),
            "partition": self.last_result.get("partition"),
            "spec_targets": (self.last_result.get("spec") or {}).get("targets"),
        }, f"explaining selection {sel.get('variant_id')}"

    def _do_show(self, variant_id: Optional[str], what: str):
        if not self.last_result:
            return {"error": "no design yet"}, "nothing to show"
        table = self.last_result.get("tradeoff_table") or []
        # The tradeoff table has variant_id + config but not the full code;
        # rebuild the candidate's RTL/driver from its config.
        row = next((r for r in table if r.get("variant_id") == variant_id), None)
        if row is None:
            return {"error": f"no variant {variant_id}"}, f"no variant {variant_id}"
        cfg = row.get("config") or {}
        from ..dse.models import VariantConfig
        from ..dse.variants import build_variant_rtl, build_variant_driver
        v = VariantConfig(**{k: cfg[k] for k in (
            "pipelined", "pipeline_stages", "datapath_width", "buffer_depth",
            "clock_gating", "interface", "polling") if k in cfg})
        module = (self.last_result.get("spec") or {}).get("module_name") or "block"
        code = build_variant_driver(v, module) if what == "driver" else build_variant_rtl(v, module)
        return {"variant_id": variant_id, "what": what, "code": code,
                "measurement": row.get("measurement")}, f"showing {variant_id} {what}"

    def _do_chat(self, user_text: str):
        return {"note": "general conversation", "capabilities": [
            "run a co-design DSE (give a feature + targets)",
            "refine the last design ('smaller', 'faster', 'lower power')",
            "explain the selection ('why')",
            "show a candidate's RTL/driver ('show v3 rtl')",
        ]}, "chat"

    # ------------------------------------------------------------------
    # Step 3: stream the natural-language reply.
    # ------------------------------------------------------------------
    def _stream_reply(self, user_text: str, action_summary: str,
                      result: Any) -> Iterator[str]:
        result_block = json.dumps(result, ensure_ascii=False, default=str)[:4000]
        user = (f"User asked: {user_text}\n\nAction taken: {action_summary}\n\n"
                f"Result (JSON, may be truncated):\n{result_block}\n\n"
                f"Explain to the user concisely.")
        if self.llm is None or not self.llm.available:
            yield from self._offline_reply(user_text, action_summary, result)
            return
        try:
            yielded = False
            for chunk in self.llm.stream(_SYSTEM_REPLY, user, temperature=0.3, max_tokens=1024):
                if chunk:
                    yielded = True
                    yield chunk
            if not yielded:
                yield from self._offline_reply(user_text, action_summary, result)
        except LLMError:
            yield from self._offline_reply(user_text, action_summary, result)

    def _offline_reply(self, user_text: str, action_summary: str, result: Any) -> Iterator[str]:
        """Deterministic prose fallback when the LLM is unavailable."""
        if isinstance(result, dict) and result.get("selected"):
            sel = result["selected"]
            m = sel.get("measurement", {})
            v = sel.get("variant", {})
            yield f"（离线模式）{action_summary}。\n"
            yield f"选中 {sel.get('variant_id')}：面积 {m.get('area_cells')} cell，"
            yield f"Fmax {m.get('fmax_mhz')} MHz，延迟 {m.get('latency_cycles')} 拍，"
            yield f"功耗 {m.get('power_mw')} mW，软件代价 {m.get('sw_cost')}。\n"
            viol = (sel.get("score", {}) or {}).get("violations", [])
            if viol:
                yield f"违反目标：{', '.join(viol)}。\n"
            else:
                yield "全部目标达标。\n"
            yield f"\n{result.get('selection_rationale','')}"
            return
        if isinstance(result, dict) and "code" in result:
            yield f"（离线模式）{action_summary}：\n```{'c' if result.get('what')=='driver' else 'systemverilog'}\n"
            yield result["code"]
            yield "\n```"
            return
        yield f"（离线模式）{action_summary}。LLM 未接入，无法生成自然语言解释；结果已记录。"

    # ------------------------------------------------------------------
    # Context for the intent router
    # ------------------------------------------------------------------
    def _context_block(self) -> str:
        if not self.last_result:
            return "(no design in this session yet)"
        spec = self.last_result.get("spec", {}) or {}
        sel = self.last_result.get("selected", {}) or {}
        m = sel.get("measurement", {}) or {}
        table = self.last_result.get("tradeoff_table") or []
        rows = ", ".join(f"{r['variant_id']}(w={r['config']['datapath_width']},"
                         f"area={r['area']},lat={r['latency']},scalar={r['scalar']:.2f})"
                         for r in table[:6])
        return (f"module={spec.get('module_name')} targets={spec.get('targets')} "
                f"selected={sel.get('variant_id')} "
                f"(area={m.get('area_cells')},fmax={m.get('fmax_mhz')},"
                f"lat={m.get('latency_cycles')},pwr={m.get('power_mw')}) "
                f"candidates=[{rows}]")

    def _recent(self) -> str:
        return "\n".join(f"{m['role']}: {m['content'][:200]}" for m in self.history[-4:])
