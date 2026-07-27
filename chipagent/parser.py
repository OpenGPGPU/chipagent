"""Task parsing layer.

Converts a natural-language request into a structured :class:`TaskObject`
(Section 3.2 of the phase 1 plan). Uses a hybrid strategy: fast rule-based
extraction first, then optionally refined by an LLM call when one is
available. The rule path guarantees deterministic output for the acceptance
test cases and for offline runs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .llm import LLMClient, LLMError
from .models import TaskObject


# Keywords that signal an RTL generation task in the user request.
# "寄存器"/"register" are intentionally excluded here — they belong to the
# reg_definition domain, detected separately by _looks_like_reg.
_RTL_KEYWORDS = ("rtl", "verilog", "systemverilog", "模块", "module", "dma", "axi", "fifo")

# Signals that a "register"-mentioning request is actually a register-block
# definition task (not an RTL module that happens to contain registers).
_REG_DEFINITION_HINTS = ("定义", "definition", "字段", "field", "寄存器块", "register block", "reg file", "register file")

_MODULE_HINTS = ("axi", "dma", "fifo", "uart", "spi", "i2c", "alU", "alu", "register", "reg", "fsm")


def _snake(name: str) -> str:
    """Normalise a free-form module name to Verilog-friendly snake_case."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "generated_module"


def _extract_module_name(request: str) -> Optional[str]:
    """Best-effort extraction of a module name from the request text."""
    lowered = request.lower()
    # "module <name>" / "模块 <name>" / "为 <name> 生成"
    m = re.search(r"\bmodule\s+([a-zA-Z_][\w]*)", lowered)
    if m:
        return _snake(m.group(1))
    m = re.search(r"模块[:：\s]*([a-zA-Z_][\w\-]*)", request)
    if m:
        return _snake(m.group(1))
    m = re.search(r"为\s*([a-zA-Z_][\w\- ]*?)\s*(?:模块|生成|设计)", request)
    if m:
        return _snake(m.group(1))
    # Fall back to the first known hint word present in the text.
    for hint in _MODULE_HINTS:
        if hint in lowered:
            return hint
    return None


def _extract_constraints(request: str) -> Dict[str, Any]:
    """Pull obvious constraint signals (clock, reset, width) and the Phase 2
    non-functional targets (area / Fmax / latency / power) from the text.

    Non-functional budgets are deposited into the ``constraints`` dict under
    the keys the DSE layer reads (``area_budget`` etc.), so the lightest-touch
    path is reused — no ``TaskObject`` dataclass change (see DSE plan).
    """
    constraints: Dict[str, Any] = {}
    if re.search(r"clk|clock|时钟|时钟域", request, re.IGNORECASE):
        constraints["clock"] = True
    if re.search(r"reset|rst|复位|异步|同步", request, re.IGNORECASE):
        constraints["reset"] = True
    width = re.search(r"(\d+)\s*[-_ ]?\s*bit|\bwidth\s*[:：]?\s*(\d+)|数据宽度\s*(\d+)", request, re.IGNORECASE)
    if width:
        constraints["data_width"] = int(next(g for g in width.groups() if g))
    if "axi" in request.lower():
        constraints["interface"] = "axi"

    # Non-functional targets — supports CN + EN, with optional units (cell/LUT,
    # MHz, cycle, mW/uW). Deposited under the constraint keys the DSE scorer reads.
    area = re.search(r"面积[^\d]{0,6}(\d+)\s*(?:cell|lut|le|门)?|area[^\d]{0,6}(\d+)\s*(?:cell|lut|le)?", request, re.IGNORECASE)
    if area:
        v = next((g for g in area.groups() if g), None)
        if v:
            constraints["area_budget"] = int(v)

    fmax = re.search(r"(?:fmax|频率|主频|时钟频率)[^\d]{0,6}(\d+)\s*m?hz|(\d+)\s*mhz", request, re.IGNORECASE)
    if fmax:
        v = next((g for g in fmax.groups() if g), None)
        if v:
            constraints["fmax_target_mhz"] = int(v)

    latency = re.search(r"延迟[^\d]{0,6}(\d+)\s*(?:cycle|拍|周期)?|latency[^\d]{0,6}(\d+)\s*(?:cycle)?", request, re.IGNORECASE)
    if latency:
        v = next((g for g in latency.groups() if g), None)
        if v:
            constraints["latency_budget_cycles"] = int(v)

    power = re.search(r"功耗[^\d]{0,6}(\d+)\s*(?:mw|uw|w)?|power[^\d]{0,6}(\d+)\s*(?:mw|uw|w)?", request, re.IGNORECASE)
    if power:
        v = next((g for g in power.groups() if g), None)
        if v:
            constraints["power_budget_mw"] = int(v)

    return constraints


def _looks_like_reg(text: str) -> bool:
    """True if the request is a register-block *definition* task."""
    has_reg = "寄存器" in text or "register" in text or "reg file" in text
    return has_reg and any(h in text for h in _REG_DEFINITION_HINTS)


# Signals a soft/hardware co-design request: hardware (RTL/register) AND
# software (driver/header) or verification (testbench/sim) work in one ask.
_SW_HINTS = ("驱动", "driver", "头文件", "header", "软件", "firmware", "hal")
_VERIFY_HINTS = ("testbench", "测试", "仿真", "simulation", "验证")


def _looks_like_codesign(text: str) -> bool:
    has_hw = any(k in text for k in ("寄存器", "register", "rtl", "verilog", "模块", "module"))
    has_sw = any(h in text for h in _SW_HINTS)
    has_verify = any(h in text for h in _VERIFY_HINTS)
    # Co-design when the user explicitly asks for multiple deliverables in one go.
    asked_multiple = sum(bool(x) for x in (has_hw, has_sw, has_verify)) >= 2
    if asked_multiple:
        return True
    # Non-functional targets (area/Fmax/latency/power) signal a design-space
    # exploration ask — which is inherently HW/SW co-design (the DSE loop).
    if re.search(r"面积|area|fmax|频率|主频|延迟|latency|功耗|power", text, re.IGNORECASE):
        return True
    return False


def _parse_with_rules(request: str) -> TaskObject:
    text = request.lower()
    is_codesign = _looks_like_codesign(text)
    is_reg = (not is_codesign) and _looks_like_reg(text)
    is_rtl = (not is_codesign and not is_reg) and (
        any(kw in text for kw in _RTL_KEYWORDS) or "rtl" in text
    )
    module_name = _extract_module_name(request)
    constraints = _extract_constraints(request)
    if is_codesign:
        task_type = "hw_sw_codesign"
        if not module_name:
            module_name = "soc_block"
    elif is_reg:
        task_type = "reg_definition"
        if not module_name:
            module_name = "reg_block"
    elif is_rtl:
        task_type = "rtl_generation"
        if not module_name:
            module_name = "generated_module"
    else:
        task_type = "unknown"
    return TaskObject(
        task_type=task_type,
        module_name=module_name,
        description=request,
        interface={"type": constraints.get("interface", "generic")},
        constraints=constraints,
        output_requirements={"language": "verilog"},
    )


_PARSE_SYSTEM_PROMPT = (
    "You are a chip-design task parser. Given a natural-language request, "
    "extract a JSON object with keys: task_type (one of 'rtl_generation', "
    "'reg_definition', 'hw_sw_codesign', 'verification', 'unknown'), module_name (snake_case "
    "Verilog identifier or null), interface (object), constraints (object), "
    "output_requirements (object). Respond with JSON only."
)


def _parse_with_llm(client: LLMClient, request: str, fallback: TaskObject) -> TaskObject:
    try:
        raw = client.chat(_PARSE_SYSTEM_PROMPT, request, temperature=0.0, max_tokens=512)
    except LLMError:
        return fallback
    data = client.extract_json(raw)
    if not data:
        return fallback
    return TaskObject(
        task_type=data.get("task_type", fallback.task_type),
        module_name=_snake(data["module_name"]) if data.get("module_name") else fallback.module_name,
        description=request,
        interface=data.get("interface") or fallback.interface,
        constraints=data.get("constraints") or fallback.constraints,
        output_requirements=data.get("output_requirements") or fallback.output_requirements,
    )


class TaskParser:
    """Hybrid NL -> TaskObject parser (rules first, LLM refinement optional)."""

    def __init__(self, llm: Optional[LLMClient] = None, use_llm: bool = True) -> None:
        self._llm = llm
        self._use_llm = use_llm and llm is not None and llm.available

    def parse(self, request: str) -> TaskObject:
        task = _parse_with_rules(request)
        if self._use_llm:
            task = _parse_with_llm(self._llm, request, task)
        # Canonicalise: known generation tasks must always carry a module_name.
        if task.task_type == "rtl_generation" and not task.module_name:
            task.module_name = "generated_module"
        if task.task_type == "reg_definition" and not task.module_name:
            task.module_name = "reg_block"
        if task.task_type == "hw_sw_codesign" and not task.module_name:
            task.module_name = "soc_block"
        return task


def parse_request(request: str) -> Dict[str, Any]:
    """Backward-compatible functional API used by the legacy workflow entry."""
    return TaskParser().parse(request).to_dict()
