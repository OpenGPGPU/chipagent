"""Structured VCD/FST failure analysis for RTL debug.

This tool deliberately does not try to infer design intent.  Callers describe
the ready/valid channels they care about; ChipAgent then reports facts proven
by the waveform and emits compact JSON/Markdown evidence for an LLM or human.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, TextIO, Tuple

from chipagent.toolchain import which_tool

from .base import Tool, ToolContext, ToolResult, persist_tool_artifacts, trust_metadata


@dataclass(frozen=True)
class VcdSignal:
    code: str
    width: int
    name: str


class VcdReader:
    """Small streaming VCD reader retaining only requested signals."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.signals: List[VcdSignal] = []
        self.timescale = ""

    def read_header(self) -> None:
        scopes: List[str] = []
        reading_timescale = False
        for raw in self.stream:
            line = raw.strip()
            if reading_timescale:
                if line == "$end":
                    reading_timescale = False
                elif line:
                    self.timescale = line
                continue
            if line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    scopes.append(parts[2])
            elif line.startswith("$upscope"):
                if scopes:
                    scopes.pop()
            elif line.startswith("$timescale"):
                text = line.replace("$timescale", "").replace("$end", "").strip()
                if text:
                    self.timescale = text
                if "$end" not in line:
                    reading_timescale = True
            elif line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 6:
                    width, code, reference = int(parts[2]), parts[3], parts[4]
                    suffix = "" if parts[5] == "$end" else parts[5]
                    name = ".".join([*scopes, f"{reference}{suffix}"])
                    self.signals.append(VcdSignal(code, width, name))
            elif line.startswith("$enddefinitions"):
                return
        raise ValueError("VCD is missing $enddefinitions")

    def samples(self, selected_codes: set[str]) -> Iterator[Tuple[int, Dict[str, str]]]:
        time = 0
        changed: Dict[str, str] = {}
        in_dump = False
        for raw in self.stream:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                if changed:
                    yield time, changed
                    changed = {}
                time = int(line[1:])
                continue
            if line.startswith("$dump"):
                in_dump = True
                continue
            if line == "$end" and in_dump:
                in_dump = False
                continue
            if line.startswith("$"):
                continue
            if line[0] in "01xXzZ":
                code, value = line[1:], line[0].lower()
            elif line[0] in "bBrR":
                parts = line.split()
                if len(parts) != 2:
                    continue
                value, code = parts[0][1:].lower(), parts[1]
            else:
                continue
            if code in selected_codes:
                changed[code] = value
        if changed:
            yield time, changed


def _resolve_signal(query: str, signals: Iterable[VcdSignal]) -> Tuple[Optional[VcdSignal], List[str]]:
    signal_list = list(signals)
    exact = [sig for sig in signal_list if sig.name == query]
    if len(exact) == 1:
        return exact[0], []
    # VCD writers commonly append a packed range (``id[3:0]``) even when the
    # RTL/source-map name is simply ``id``.
    ranged = [sig for sig in signal_list
              if re.sub(r"\[[^]]+\]$", "", sig.name) == query]
    if len(ranged) == 1:
        return ranged[0], []
    suffix = [sig for sig in signal_list
              if sig.name.endswith("." + query) or sig.name == query or
              re.sub(r"\[[^]]+\]$", "", sig.name).endswith("." + query)]
    if len(suffix) == 1:
        return suffix[0], []
    if not suffix:
        return None, [f"signal not found: {query}"]
    return None, [f"ambiguous signal {query}: {', '.join(sig.name for sig in suffix[:8])}"]


def _is_one(value: Optional[str]) -> bool:
    return value is not None and set(value) == {"1"}


def _has_unknown(value: Optional[str]) -> bool:
    return value is not None and bool(set(value) & {"x", "z"})


def _as_hex(value: Optional[str]) -> str:
    if value is None:
        return "?"
    if _has_unknown(value):
        return value
    try:
        return f"0x{int(value, 2):x}"
    except ValueError:
        return value


class AnalyzeWaveformTool(Tool):
    name = "analyze_waveform"

    def run(self, ctx: ToolContext) -> ToolResult:
        path_text = str(ctx.inputs.get("waveform_path") or ctx.inputs.get("vcd_path") or "")
        protocols = ctx.inputs.get("protocols") or []
        if not path_text:
            return ToolResult(
                result={"status": "error", "source": "waveform"},
                issues=["missing waveform_path input"],
            )
        source = Path(path_text).expanduser()
        if not source.exists():
            return ToolResult(
                result={"status": "error", "source": "waveform", "waveform_path": str(source)},
                issues=[f"waveform does not exist: {source}"],
            )
        try:
            vcd_path, converted = self._ensure_vcd(source)
            analysis = self._analyze(vcd_path, protocols, ctx.inputs)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return ToolResult(
                result={"status": "error", "source": "waveform", "waveform_path": str(source)},
                issues=[str(exc)],
            )
        analysis["waveform_path"] = str(source)
        analysis["converted_from_fst"] = converted
        markdown = self._markdown(analysis)
        artifacts = persist_tool_artifacts(ctx, self.name, {
            "failure.json": json.dumps(analysis, ensure_ascii=False, indent=2),
            "timeline.md": markdown,
        })
        analysis["artifacts"] = artifacts
        analysis.update(trust_metadata(
            source="waveform", tool="vcd_stream_parser", tool_available=True,
            artifacts=artifacts,
        ))
        return ToolResult(result=analysis, issues=[])

    @staticmethod
    def _ensure_vcd(source: Path) -> Tuple[Path, bool]:
        if source.suffix.lower() != ".fst":
            return source, False
        fst2vcd = which_tool("fst2vcd")
        if not fst2vcd:
            raise ValueError("FST input requires fst2vcd on PATH; provide VCD or install gtkwave tools")
        target = Path(tempfile.mkdtemp(prefix="chipagent-wave-")) / "converted.vcd"
        with target.open("w", encoding="utf-8") as out:
            subprocess.run([fst2vcd, str(source)], stdout=out, check=True, timeout=120)
        return target, True

    def _analyze(self, path: Path, protocols: List[Dict[str, Any]], inputs: Dict[str, Any]) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            reader = VcdReader(stream)
            reader.read_header()
            resolved: List[Dict[str, Any]] = []
            errors: List[str] = []
            codes: set[str] = set()
            for index, raw in enumerate(protocols):
                protocol = dict(raw)
                mapping: Dict[str, VcdSignal] = {}
                requested = {
                    "clock": protocol.get("clock"),
                    "valid": protocol.get("valid"),
                    "ready": protocol.get("ready"),
                    "id": protocol.get("id"),
                }
                for payload_index, name in enumerate(protocol.get("payload") or []):
                    requested[f"payload:{payload_index}"] = name
                for role, name in requested.items():
                    if not name:
                        continue
                    signal, signal_errors = _resolve_signal(str(name), reader.signals)
                    errors.extend(signal_errors)
                    if signal:
                        mapping[role] = signal
                        codes.add(signal.code)
                if "valid" not in mapping or "ready" not in mapping:
                    errors.append(f"protocol {protocol.get('name', index)} requires valid and ready")
                    continue
                resolved.append({"spec": protocol, "signals": mapping, "state": {}})

            failure_time = inputs.get("failure_time")
            before = int(inputs.get("window_before", 100))
            after = int(inputs.get("window_after", 20))
            start = max(0, int(failure_time) - before) if failure_time is not None else 0
            end = int(failure_time) + after if failure_time is not None else None
            values: Dict[str, str] = {}
            transactions: List[Dict[str, Any]] = []
            findings: List[Dict[str, Any]] = []
            timeline: List[Dict[str, Any]] = []
            last_time = start
            for time, changes in reader.samples(codes):
                values.update(changes)
                if time < start:
                    continue
                if end is not None and time > end:
                    break
                last_time = time
                for channel in resolved:
                    self._sample_channel(time, changes, values, channel,
                                         transactions, findings, timeline)

            for channel in resolved:
                state = channel["state"]
                if state.get("stall_start") is not None:
                    self._record_timeout(channel, state, last_time,
                                         findings, still_stalled=True)
            self._correlate_transactions(resolved, transactions, findings, last_time)
            channel_sources = {
                str(item["spec"].get("name") or "channel"): item["spec"].get("source")
                for item in resolved if item["spec"].get("source")
            }
            for finding in findings:
                source = channel_sources.get(str(finding.get("channel") or ""))
                if source:
                    finding["source"] = source
            status = "issues_found" if findings or errors else "clean"
            return {
                "status": status,
                "timescale": reader.timescale,
                "window": {"start": start, "end": end},
                "protocols_analyzed": len(resolved),
                "transactions": transactions,
                "findings": findings,
                "timeline": timeline,
                "configuration_issues": errors,
            }

    def _sample_channel(self, time: int, changes: Dict[str, str], values: Dict[str, str],
                        channel: Dict[str, Any], transactions: List[Dict[str, Any]],
                        findings: List[Dict[str, Any]], timeline: List[Dict[str, Any]]) -> None:
        spec, signals, state = channel["spec"], channel["signals"], channel["state"]
        name = str(spec.get("name") or "channel")
        valid = values.get(signals["valid"].code)
        ready = values.get(signals["ready"].code)
        payload = {role: values.get(sig.code) for role, sig in signals.items()
                   if role.startswith("payload:") or role == "id"}
        relevant = {sig.code for sig in signals.values()}
        if not (set(changes) & relevant):
            return
        if "clock" in signals:
            clock = values.get(signals["clock"].code)
            previous_clock = state.get("clock")
            state["clock"] = clock
            rising_edge = clock == "1" and previous_clock == "0"
            if not rising_edge:
                if state.get("stall_start") is not None:
                    old = state.get("stall_payload", {})
                    changed_payload = [role for role, value in payload.items()
                                       if old.get(role) is not None and value != old.get(role)]
                    if changed_payload:
                        findings.append({"type": "payload_changed_while_stalled",
                                         "channel": name, "time": time,
                                         "signals": changed_payload,
                                         "before": {k: _as_hex(v) for k, v in old.items()},
                                         "after": {k: _as_hex(v) for k, v in payload.items()}})
                        state["stall_payload"] = dict(payload)
                return
        for role, value in {"valid": valid, "ready": ready, **payload}.items():
            if _has_unknown(value):
                key = (role, time)
                if key not in state.setdefault("unknown_seen", set()):
                    state["unknown_seen"].add(key)
                    findings.append({"type": "unknown_value", "channel": name,
                                     "time": time, "signal": role, "value": value})

        stalled = _is_one(valid) and not _is_one(ready)
        if stalled and state.get("stall_start") is None:
            state["stall_start"] = time
            state["stall_payload"] = dict(payload)
            timeline.append({"time": time, "channel": name, "event": "stall_start",
                             "id": _as_hex(payload.get("id"))})
        elif stalled:
            old = state.get("stall_payload", {})
            changed_payload = [role for role, value in payload.items()
                               if old.get(role) is not None and value != old.get(role)]
            if changed_payload:
                findings.append({"type": "payload_changed_while_stalled", "channel": name,
                                 "time": time, "signals": changed_payload,
                                 "before": {k: _as_hex(v) for k, v in old.items()},
                                 "after": {k: _as_hex(v) for k, v in payload.items()}})
                state["stall_payload"] = dict(payload)
        elif state.get("stall_start") is not None:
            self._record_timeout(channel, state, time, findings, still_stalled=False)
            timeline.append({"time": time, "channel": name, "event": "stall_end",
                             "duration": time - state["stall_start"]})
            state["stall_start"] = None

        handshake = _is_one(valid) and _is_one(ready)
        previous_handshake = state.get("handshake", False)
        # A handshake is recorded once per contiguous sampled assertion. Clocked
        # protocols should include a clock-sampled VCD or toggle valid/ready per beat.
        if handshake and ("clock" in signals or not previous_handshake):
            transaction = {"time": time, "channel": name,
                           "id": _as_hex(payload.get("id")),
                           "payload": {k: _as_hex(v) for k, v in payload.items()
                                       if k != "id"}}
            transactions.append(transaction)
            timeline.append({**transaction, "event": "handshake"})
        state["handshake"] = handshake

    @staticmethod
    def _correlate_transactions(channels: List[Dict[str, Any]],
                                transactions: List[Dict[str, Any]],
                                findings: List[Dict[str, Any]],
                                last_time: int) -> None:
        specs = {str(item["spec"].get("name") or "channel"): item["spec"]
                 for item in channels}
        outstanding: Dict[Tuple[str, str], List[int]] = {}
        for transaction in sorted(transactions, key=lambda item: item["time"]):
            spec = specs.get(transaction["channel"], {})
            role = str(spec.get("role") or "").lower()
            pair = str(spec.get("pair") or "")
            txn_id = str(transaction.get("id") or "?")
            if role not in {"request", "response"} or not pair:
                continue
            key = (pair, txn_id)
            if role == "request":
                if outstanding.get(key):
                    findings.append({"type": "duplicate_outstanding_id",
                                     "channel": transaction["channel"],
                                     "pair": pair, "id": txn_id,
                                     "time": transaction["time"],
                                     "first_request": outstanding[key][0]})
                outstanding.setdefault(key, []).append(int(transaction["time"]))
                continue
            if not outstanding.get(key):
                findings.append({"type": "response_without_request",
                                 "channel": transaction["channel"],
                                 "pair": pair, "id": txn_id,
                                 "time": transaction["time"]})
                continue
            request_time = outstanding[key].pop(0)
            if not outstanding[key]:
                del outstanding[key]
            request_spec = next((candidate for candidate in specs.values()
                                 if str(candidate.get("pair") or "") == pair and
                                 str(candidate.get("role") or "").lower() == "request"), {})
            limit = int(request_spec.get("max_response_time", 0) or 0)
            latency = int(transaction["time"]) - request_time
            if limit and latency > limit:
                findings.append({"type": "response_timeout", "channel": transaction["channel"],
                                 "pair": pair, "id": txn_id,
                                 "request_time": request_time,
                                 "response_time": transaction["time"],
                                 "latency": latency, "limit": limit})
        for (pair, txn_id), request_times in outstanding.items():
            for request_time in request_times:
                findings.append({"type": "missing_response", "channel": pair,
                                 "pair": pair, "id": txn_id,
                                 "request_time": request_time,
                                 "time": last_time,
                                 "age": last_time - request_time})

    @staticmethod
    def _record_timeout(channel: Dict[str, Any], state: Dict[str, Any], time: int,
                        findings: List[Dict[str, Any]], still_stalled: bool) -> None:
        limit = int(channel["spec"].get("max_stall_time", 0) or 0)
        duration = time - int(state["stall_start"])
        if limit and duration > limit:
            findings.append({"type": "stall_timeout",
                             "channel": str(channel["spec"].get("name") or "channel"),
                             "time": time, "start": state["stall_start"],
                             "duration": duration, "limit": limit,
                             "still_stalled": still_stalled})

    @staticmethod
    def _markdown(data: Dict[str, Any]) -> str:
        lines = ["# Waveform debug timeline", "",
                 f"Status: `{data['status']}`", f"Timescale: `{data.get('timescale') or 'unknown'}`", ""]
        if data.get("configuration_issues"):
            lines += ["## Configuration issues", ""] + [
                f"- {issue}" for issue in data["configuration_issues"]] + [""]
        lines += ["## Findings", ""]
        if not data.get("findings"):
            lines.append("- No configured protocol violation was observed.")
        else:
            for finding in data["findings"]:
                lines.append(f"- t={finding.get('time')}: `{finding['type']}` on `{finding['channel']}`")
        lines += ["", "## Event timeline", "", "| Time | Channel | Event | ID |", "|---:|---|---|---|"]
        for event in data.get("timeline", []):
            lines.append(f"| {event.get('time')} | {event.get('channel')} | {event.get('event')} | {event.get('id', '')} |")
        return "\n".join(lines) + "\n"
