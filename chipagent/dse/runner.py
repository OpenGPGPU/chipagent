"""The DSE agent loop (Phase 2): propose → build → measure → score → reflect.

:func:`run_dse` is the public entry. It parses a natural-language request,
extracts non-functional targets, runs the design-space search with a
config-sensitive stub backend, and returns the selected design plus a Pareto
front and a tradeoff table — the artifact a one-shot LLM cannot produce.

The loop is an explicit Python ``while`` (not LangGraph): variable-length
iterative search with reflection is cleaner as a plain loop. Existing
primitives are reused: :class:`TaskParser`, :class:`Repository`,
:class:`AuditLog`, and the stub measurer / scorer / proposer / reflector in
this package.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..llm import LLMClient
from ..logging_utils import append_log
from ..models import TaskStatus, TaskObject
from ..parser import TaskParser
from ..repository import Repository
from ..config import Settings
from ..validation import VerilogLinter
from .measurer import BackendMeasurer, default_measurer
from .models import (Candidate, DesignSpec, NonFunctionalTargets, Partition,
                     VariantConfig)
from .proposer import PartitionProposer, Reflector
from .scorer import ParetoFront, Scorer
from .variants import build_variant_driver, build_variant_rtl


class DSERunner:
    """Owns the collaborators for a DSE run."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        llm: Optional[LLMClient] = None,
        parser: Optional[TaskParser] = None,
        repository: Optional[Repository] = None,
        linter: Optional[VerilogLinter] = None,
        measurer: Optional[BackendMeasurer] = None,
        proposer: Optional[PartitionProposer] = None,
        reflector: Optional[Reflector] = None,
        scorer: Optional[Scorer] = None,
        use_llm: bool = True,
        max_iterations: int = 4,
        max_candidates: int = 12,
    ) -> None:
        self.settings = settings or Settings.load()
        self.llm = llm or LLMClient(self.settings.llm)
        self.parser = parser or TaskParser(self.llm, use_llm=use_llm)
        self.repository = repository or Repository(
            repo_root=self.settings.repo_root, use_git=self.settings.use_git)
        self.linter = linter or VerilogLinter()
        self.measurer = measurer or default_measurer()
        self.proposer = proposer or PartitionProposer(self.llm if use_llm else None)
        self.reflector = reflector or Reflector(self.llm if use_llm else None)
        self.scorer = scorer or Scorer()
        self.pareto = ParetoFront()
        self.max_iterations = max_iterations
        self.max_candidates = max_candidates
        self._all: List[Candidate] = []
        self._seen: set[str] = set()
        self.logs: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _spec_from_task(self, task: TaskObject) -> DesignSpec:
        targets = NonFunctionalTargets.from_constraints(task.constraints or {})
        # Derive functional ops from the description / interface heuristically.
        ops = ["datapath", "control", "status"]
        iface = (task.interface or {}).get("type", "generic")
        if iface in ("axi", "streaming"):
            ops.append("dma_move")
        if "dma" in (task.module_name or "").lower():
            ops.append("dma_move")
        return DesignSpec(
            module_name=task.module_name or "soc_block",
            description=task.description,
            functional_ops=ops,
            targets=targets,
        )

    def _candidate(self, vid: str, partition: Partition, cfg: VariantConfig,
                   spec: DesignSpec) -> Candidate:
        rtl = build_variant_rtl(cfg, spec.module_name)
        driver = build_variant_driver(cfg, spec.module_name)
        # Structural lint as a cheap validity gate (does not block measurement).
        lint = self.linter.run(rtl)
        m = self.measurer.measure(cfg, module=spec.module_name)
        score = self.scorer.score(m, spec.targets)
        rationale = (f"variant {vid}: {cfg.signature().replace('|', ' ')}; "
                     f"lint={lint['lint']}")
        return Candidate(variant_id=vid, partition=partition, variant=cfg,
                         rtl=rtl, driver=driver, measurement=m, score=score,
                         rationale=rationale)

    # ------------------------------------------------------------------
    def run(
        self,
        request: str,
        *,
        output_dir: Optional[str] = None,
        targets_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        logs = self.logs
        # 1. spec_parse
        task = self.parser.parse(request).to_dict()
        if targets_override:
            constraints = dict(task.get("constraints") or {})
            constraints.update(_coerce_targets(targets_override))
            task["constraints"] = constraints
        tobj = TaskObject(
            task_type="hw_sw_codesign",  # DSE is inherently HW/SW co-design
            module_name=task.get("module_name"),
            description=task.get("description", request),
            interface=task.get("interface", {}) or {},
            constraints=task.get("constraints", {}) or {},
            output_requirements=task.get("output_requirements", {}) or {},
        )
        spec = self._spec_from_task(tobj)
        logs.append(append_log([], "spec_parse", "completed", spec.to_dict()))

        # 2. propose
        partition = self.proposer.initial_partition(spec)
        configs = self.proposer.seed_configs(spec)
        logs.append(append_log([], "propose", "completed",
                               {"partition": partition.to_dict(),
                                "seeds": len(configs)}))

        # 3. search loop
        iteration = 0
        no_improve = 0
        best_scalar = -1.0
        pending = list(configs)
        vid = 0
        while iteration < self.max_iterations and len(self._all) < self.max_candidates:
            iteration += 1
            new_cands: List[Candidate] = []
            for cfg in pending:
                sig = cfg.signature()
                if sig in self._seen:
                    continue
                if len(self._all) + len(new_cands) >= self.max_candidates:
                    break
                self._seen.add(sig)
                vid += 1
                c = self._candidate(f"v{vid}", partition, cfg, spec)
                self._all.append(c)
                new_cands.append(c)
            self.pareto.update(new_cands)
            self.pareto.assign_ranks(self._all)

            cur_best = max((c.score.scalar for c in self._all), default=-1.0)
            improved = cur_best > best_scalar + 1e-6
            best_scalar = max(best_scalar, cur_best)
            logs.append(append_log([], f"iter{iteration}", "measured",
                                   {"new": len(new_cands),
                                    "front": len(self.pareto.members),
                                    "best_scalar": round(best_scalar, 3)}))

            # convergence: best scalar stopped improving for 2 iters
            if new_cands and not improved:
                no_improve += 1
            elif new_cands:
                no_improve = 0
            if no_improve >= 2:
                logs.append(append_log([], "converged", "no_improvement", None))
                break

            # reflect: revise the worst-violated candidates toward their weakest
            # dimension, chaining the rule past already-seen signatures so the
            # search actually moves (e.g. width 8 -> 16 -> 32 -> 64).
            pending = self._reflect_revisions(spec)
            if not pending:
                break  # nothing left to try

        # 4. select
        selected = self._select(spec)
        logs.append(append_log([], "select", "completed",
                               {"variant_id": selected.variant_id if selected else None}))

        # 5. persist + audit
        artifacts: Dict[str, Any] = {}
        if selected is not None and output_dir:
            files = {
                f"{spec.module_name}_dut.sv": selected.rtl,
                f"{spec.module_name}_driver.c": selected.driver,
            }
            report_data = {"spec": spec.to_dict(), "selected": selected.to_dict(),
                           "pareto": [c.to_dict() for c in self.pareto.members],
                           "tradeoff_table": self._tradeoff_table()}
            artifacts = self.repository.persist_artifacts(
                output_dir=output_dir, files=files, report_data=report_data,
                task=task, logs=logs, log_dir=self.settings.default_log_dir,
            )
            self._audit(spec, selected, artifacts)
            logs.append(append_log([], "persist", "completed", list(artifacts.keys())))

        status = TaskStatus.COMPLETED.value if selected else TaskStatus.FAILED.value
        return {
            "status": status,
            "task_type": "hw_sw_codesign",
            "spec": spec.to_dict(),
            "partition": partition.to_dict(),
            "selected": selected.to_dict() if selected else {},
            "pareto": [c.to_dict() for c in self.pareto.members],
            "tradeoff_table": self._tradeoff_table(),
            "iterations": iteration,
            "partition_rationale": partition.rationale,
            "selection_rationale": (selected.rationale if selected else "no feasible candidate"),
            "logs": logs,
            "artifacts": artifacts,
        }

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _reflect_revisions(self, spec: DesignSpec) -> List[VariantConfig]:
        """Produce novel revised configs from the worst-violated candidates.

        For each of the top candidates (by scalar) that still violate a
        dimension, chain the reflector's revision rule past already-seen
        signatures so the search moves along the design axis instead of
        stalling on a neighbour it has already sampled.
        """
        out: List[VariantConfig] = []
        # Order by scalar; prefer violated candidates.
        ranked = sorted(self._all, key=lambda c: c.score.scalar, reverse=True)
        violated = [c for c in ranked if c.score.violations] or ranked[:3]
        for c in violated[:4]:
            worst = self.scorer.worst_dimension(c.score)
            if worst is None:
                continue
            cur = c.variant
            for _ in range(5):  # chain up to 5 steps to escape seen neighbours
                revised = self.reflector.revise(cur, worst, c.measurement)
                if revised is None:
                    break
                if (revised.signature() != c.variant.signature()
                        and revised.signature() not in self._seen):
                    out.append(revised)
                    self.logs.append(append_log([], "reflect", "revised", {
                        "from": c.variant_id, "worst": worst,
                        "to": revised.signature()}))
                    break
                cur = revised
            if len(out) >= 3:
                break
        return out

    def _best_feasible_or_scalar(self) -> Optional[Candidate]:
        feasible = [c for c in self._all if not c.score.violations]
        pool = feasible or self._all
        if not pool:
            return None
        # Fewest violations first, then highest scalar, then smallest area.
        return sorted(pool, key=lambda c: (
            len(c.score.violations), -c.score.scalar, c.measurement.area_cells))[0]

    def _select(self, spec: DesignSpec) -> Optional[Candidate]:
        """Feasibility-first: fewest violations, then highest scalar, then
        smallest area. Search the Pareto front first, then fall back to all."""
        front = self.pareto.members
        pool = front or self._all
        feasible = [c for c in pool if not c.score.violations]
        pool = feasible or pool
        if not pool:
            return None
        return sorted(pool, key=lambda c: (
            len(c.score.violations), -c.score.scalar, c.measurement.area_cells))[0]

    def _tradeoff_table(self) -> List[Dict[str, Any]]:
        rows = []
        for c in sorted(self._all, key=lambda x: x.score.scalar, reverse=True):
            rows.append({
                "variant_id": c.variant_id,
                "config": c.variant.to_dict(),
                "area": c.measurement.area_cells,
                "fmax": c.measurement.fmax_mhz,
                "latency": c.measurement.latency_cycles,
                "power": c.measurement.power_mw,
                "sw_cost": c.measurement.sw_cost,
                "scalar": c.score.scalar,
                "pareto_rank": c.score.pareto_rank,
                "violations": c.score.violations,
            })
        return rows

    def _audit(self, spec: DesignSpec, selected: Candidate, artifacts: Dict[str, Any]) -> None:
        try:
            from ..audit import AuditLog
            import os
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            log_dir = self.settings.default_log_dir
            os.makedirs(log_dir, exist_ok=True)
            al = AuditLog(os.path.join(log_dir, "dse_audit.jsonl"))
            for c in self._all:
                al.record_artifact(
                    task_id=spec.module_name, timestamp=ts,
                    artifact=c.variant_id, produced_by=f"dse:variant:{c.variant_id}",
                    inputs={"config": c.variant.to_dict(),
                            "measurement": c.measurement.to_dict()})
            if selected:
                al.record_artifact(
                    task_id=spec.module_name, timestamp=ts,
                    artifact="selected", produced_by=f"dse:select:{selected.variant_id}",
                    inputs={"artifacts": list(artifacts.keys())})
        except Exception:  # pragma: no cover - audit is best-effort
            pass


def _coerce_targets(override: Dict[str, Any]) -> Dict[str, Any]:
    """Map CLI --targets keys (area=, fmax=, latency=, power=, sw_cost=) to the
    constraint keys the measurer/scorer read."""
    mapping = {
        "area": "area_budget", "area_budget": "area_budget",
        "fmax": "fmax_target_mhz", "fmax_target_mhz": "fmax_target_mhz",
        "latency": "latency_budget_cycles", "latency_budget_cycles": "latency_budget_cycles",
        "power": "power_budget_mw", "power_budget_mw": "power_budget_mw",
        "sw_cost": "sw_cost_budget", "sw_cost_budget": "sw_cost_budget",
    }
    out: Dict[str, Any] = {}
    for k, v in override.items():
        key = mapping.get(k, k)
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def run_dse(
    request: str,
    *,
    output_dir: Optional[str] = None,
    targets: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Run the DSE agent loop end-to-end and return the selected design +
    Pareto front + tradeoff table.

    ``targets`` is an optional dict (e.g. ``{"area": 300, "fmax": 250}``)
    overriding the non-functional budgets; these take precedence over anything
    the parser extracted from the request.
    """
    runner = DSERunner(use_llm=use_llm)
    return runner.run(request, output_dir=output_dir, targets_override=targets)


def main() -> None:
    """CLI entry: ``python -m chipagent.dse.runner "<request>" [--targets ...]``."""
    import argparse
    import json
    from pathlib import Path

    p = argparse.ArgumentParser(description="Run the ChipAgent DSE agent loop")
    p.add_argument("request")
    p.add_argument("--output-dir", dest="output_dir")
    p.add_argument("--targets", help="area=300,fmax=250,latency=4,power=15,sw_cost=0.3")
    p.add_argument("--no-llm", dest="no_llm", action="store_true")
    args = p.parse_args()

    targets = None
    if args.targets:
        targets = {}
        for pair in args.targets.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                targets[k.strip()] = v.strip()
    output_dir = args.output_dir or str(Path("generated") / "dse")
    result = run_dse(args.request, output_dir=output_dir, targets=targets,
                     use_llm=not args.no_llm)
    # Print a concise summary + the tradeoff table.
    print(json.dumps({
        "status": result["status"],
        "selected": result.get("selected", {}).get("variant_id"),
        "selection_rationale": result.get("selection_rationale"),
        "partition_rationale": result.get("partition_rationale"),
        "iterations": result.get("iterations"),
        "pareto_count": len(result.get("pareto", [])),
        "tradeoff_table": result.get("tradeoff_table"),
        "artifacts": result.get("artifacts"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
