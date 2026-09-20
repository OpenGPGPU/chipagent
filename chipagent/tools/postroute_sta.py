"""Post-route STA on ORFS route artifacts."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from chipagent.toolchain import configured_openroad_image, docker_image_status
from chipagent.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    missing_tool_result,
    trust_metadata,
)

_PLATFORM_LIBS = (
    "asap7sc7p5t_AO_{vt}_TT_nldm_211120.lib.gz",
    "asap7sc7p5t_INVBUF_{vt}_TT_nldm_220122.lib.gz",
    "asap7sc7p5t_OA_{vt}_TT_nldm_220123.lib",
    "asap7sc7p5t_SEQ_{vt}_TT_nldm_220123.lib",
    "asap7sc7p5t_SIMPLE_{vt}_TT_nldm_211120.lib.gz",
)


def _resolve_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    phys_dir = inputs.get("physical_output_dir")
    if phys_dir:
        base = Path(str(phys_dir)).expanduser().resolve()
        results = base / "orfs-work" / "results" / "base"
        designs = base / "orfs-work" / "designs" / "asap7"
        modules = sorted(p for p in designs.iterdir() if p.is_dir()) if designs.is_dir() else []
        module_dir = modules[0] if len(modules) == 1 else None
        if inputs.get("module_name") and designs.is_dir():
            candidate = designs / str(inputs["module_name"])
            module_dir = candidate if candidate.is_dir() else module_dir
        odb = _first_existing(results, ["5_2_route.odb", "5_route.odb", "*.odb"])
        spef = _first_existing(results, ["6_final.spef", "*.spef"])
        if odb:
            resolved["odb"] = odb
        if spef:
            resolved["spef"] = spef
        if module_dir is not None:
            sdc = _first_existing(module_dir, ["constraint.sdc", "*.sdc"])
            if sdc:
                resolved["sdc"] = sdc
            resolved["macro_dir"] = str(module_dir / "macros") if (module_dir / "macros").is_dir() else None
            resolved["module_name"] = module_dir.name
    for key in ("odb", "spef", "sdc"):
        if inputs.get(key):
            resolved[key] = str(Path(str(inputs[key])).expanduser().resolve())
    if inputs.get("macro_dir"):
        resolved["macro_dir"] = str(Path(str(inputs["macro_dir"])).expanduser().resolve())
    return resolved


def _first_existing(directory: Path, patterns: List[str]) -> Optional[str]:
    if not directory.is_dir():
        return None
    for pattern in patterns:
        matches = sorted(directory.glob(pattern))
        existing = [m for m in matches if m.is_file()]
        if existing:
            return str(existing[-1])
    return None


def _postroute_sta_tcl(
    *,
    odb_name: str,
    spef_name: Optional[str],
    sdc_name: str,
    cell_vt: str,
    extra_lib_names: List[str],
    max_paths: int,
) -> str:
    vt = cell_vt.upper()
    lines = [
        f"read_liberty /orfs/pdklib/{name.format(vt=vt)}"
        for name in _PLATFORM_LIBS
    ]
    lines.append("foreach lib [glob -nocomplain /orfs/design/macros/*.lib] { read_liberty $lib }")
    for name in extra_lib_names:
        lines.append(f"read_liberty /orfs/extralibs/{name}")
    lines.append(f"read_db /orfs/results/{odb_name}")
    if spef_name:
        lines.append(f"read_spef /orfs/results/{spef_name}")
    lines.append(f"read_sdc /orfs/design/{sdc_name}")
    lines.append("report_wns -digits 3")
    lines.append("report_tns -digits 3")
    lines.append(f"report_checks -path_delay max -digits 3 -group_path_count {max_paths}")
    lines.append(f"report_checks -path_delay min -digits 3 -group_path_count {max_paths}")
    return "\n".join(lines) + "\n"


def _is_boundary_path(startpoint: str, endpoint: str) -> bool:
    return "input port" in startpoint or "output port" in endpoint


def _parse_sta_report(text: str) -> Dict[str, Any]:
    header = re.compile(
        r"^Startpoint:\s*(?P<start>[^\n]+)\n.*?"
        r"^Endpoint:\s*(?P<endpoint>[^\n]+)\n.*?"
        r"^Path Group:\s*(?P<group>\S+)\n"
        r"^Path Type:\s*(?P<type>\S+)\n"
        r".*?^\s*(?P<slack>[-\d.]+)\s+slack\s+\((?P<verdict>VIOLATED|MET)\)\s*$",
        re.MULTILINE | re.DOTALL,
    )
    summaries: Dict[str, Any] = {}
    for kind in ("max", "min"):
        paths = []
        for match in header.finditer(text):
            if match.group("type") != kind:
                continue
            paths.append({
                "startpoint": " ".join(match.group("start").split()),
                "endpoint": " ".join(match.group("endpoint").split()),
                "group": match.group("group"),
                "slack_ps": float(match.group("slack")),
                "violated": match.group("verdict") == "VIOLATED",
            })
        violated = [p for p in paths if p["violated"]]
        internal = [p for p in violated if not _is_boundary_path(p["startpoint"], p["endpoint"])]
        boundary = [p for p in violated if _is_boundary_path(p["startpoint"], p["endpoint"])]
        per_group: Dict[str, Any] = {}
        for path in violated:
            group = path["group"]
            current = per_group.get(group)
            if current is None or path["slack_ps"] < current["slack_ps"]:
                per_group[group] = {
                    "startpoint": path["startpoint"],
                    "endpoint": path["endpoint"],
                    "slack_ps": path["slack_ps"],
                }
        slacks = [p["slack_ps"] for p in paths]
        summaries["setup" if kind == "max" else "hold"] = {
            "wns_ps": min(slacks) if slacks else None,
            "tns_ps": round(sum(s for s in slacks if s < 0), 3) if slacks else None,
            "violations": len(violated),
            "per_group": per_group,
            "internal": {
                "wns_ps": min((p["slack_ps"] for p in internal), default=None),
                "violations": len(internal),
            },
            "boundary": {
                "wns_ps": min((p["slack_ps"] for p in boundary), default=None),
                "violations": len(boundary),
            },
        }
    return summaries


class PostRouteSTATool(Tool):
    name = "run_postroute_sta"
    description = "Post-route OpenSTA on ORFS route ODB plus extracted SPEF."
    input_schema = {
        "odb": "Routed OpenROAD database (.odb), or physical_output_dir to auto-locate it",
        "spef": "Extracted parasitics (.spef); auto-located from physical_output_dir",
        "sdc": "Timing constraints; auto-located from physical_output_dir",
        "physical_output_dir": "ChipAgent ASAP7 physical flow output directory",
        "corner": "PVT corner label for reporting",
        "cell_vt": "ASAP7 threshold flavor (RVT, LVT, SLVT)",
        "liberty_files": "Optional host Liberty overrides mounted into the STA run",
        "max_paths": "report_checks group_path_count per delay type",
    }

    def run(self, ctx: ToolContext) -> ToolResult:
        resolved = _resolve_inputs(ctx.inputs)
        odb = resolved.get("odb")
        sdc = resolved.get("sdc")
        if not odb or not Path(odb).is_file():
            return ToolResult(
                result={
                    "status": "error",
                    "message": "Post-route STA needs a routed .odb (odb or physical_output_dir).",
                    **trust_metadata(source="tool", tool="openroad-sta", tool_available=True),
                },
                issues=["Missing input: routed ODB"],
            )
        if not sdc or not Path(sdc).is_file():
            return ToolResult(
                result={
                    "status": "error",
                    "message": "Post-route STA needs an SDC (sdc or physical_output_dir).",
                    **trust_metadata(source="tool", tool="openroad-sta", tool_available=True),
                },
                issues=["Missing input: SDC"],
            )
        image = configured_openroad_image()
        docker = docker_image_status(image)
        if not docker.get("cli_available") or not docker.get("usable"):
            return missing_tool_result(
                "openroad",
                f"OpenROAD ORFS image is unavailable. {docker.get('error') or ''}",
                install_url="https://openroad-flow-scripts.readthedocs.io/en/latest/user/DockerShell.html",
            )
        cell_vt = str(ctx.inputs.get("cell_vt") or "SLVT").upper()
        if cell_vt not in {"RVT", "LVT", "SLVT"}:
            return ToolResult(
                result={"status": "error", "message": f"Invalid cell_vt: {cell_vt}"},
                issues=["Invalid input: cell_vt (expected RVT, LVT, or SLVT)"],
            )
        max_paths = int(ctx.inputs.get("max_paths") or 200)
        if max_paths < 1:
            return ToolResult(
                result={"status": "error", "message": "max_paths must be positive"},
                issues=["Invalid input: max_paths"],
            )
        raw_libs = ctx.inputs.get("liberty_files")
        lib_paths = [raw_libs] if isinstance(raw_libs, str) else list(raw_libs or [])
        extra_lib_names = [Path(str(p)).name for p in lib_paths]
        tcl = _postroute_sta_tcl(
            odb_name=Path(odb).name,
            spef_name=Path(resolved["spef"]).name if resolved.get("spef") else None,
            sdc_name=Path(sdc).name,
            cell_vt=cell_vt,
            extra_lib_names=extra_lib_names,
            max_paths=max_paths,
        )
        work = Path(ctx.inputs.get("output_dir") or Path("generated") / "postroute_sta")
        work.mkdir(parents=True, exist_ok=True)
        (work / "sta.tcl").write_text(tcl, encoding="utf-8")
        volumes = [
            "-v", f"{Path(odb).parent.resolve()}:/orfs/results",
            "-v", f"{Path(sdc).parent.resolve()}:/orfs/design",
            "-v", f"{(work / 'sta.tcl').resolve()}:/tmp/sta.tcl",
        ]
        for lib in lib_paths:
            volumes += ["-v", f"{Path(str(lib)).parent.resolve()}:/orfs/extralibs"]
        cmd = [
            "docker", "run", "--rm", *volumes,
            image, "openroad", "/tmp/sta.tcl",
        ]
        timeout = int(ctx.inputs.get("timeout") or 600)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            report = proc.stdout + ("\n\n[stderr]\n" + proc.stderr if proc.stderr else "")
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            return ToolResult(
                result={
                    "status": "timeout",
                    "message": f"Post-route STA exceeded the {timeout}s timeout.",
                    **trust_metadata(source="tool", tool="openroad-sta", tool_available=True, command=cmd),
                },
                issues=["Post-route STA timed out"],
            )
        (work / "sta.log").write_text(report, encoding="utf-8")
        if returncode != 0:
            return ToolResult(
                result={
                    "status": "error",
                    "message": "OpenROAD STA run failed.",
                    "report_tail": "\n".join(report.splitlines()[-40:]),
                    **trust_metadata(source="tool", tool="openroad-sta", tool_available=True, command=cmd),
                },
                issues=["OpenROAD STA failed"],
            )
        parsed = _parse_sta_report(report)
        setup = parsed["setup"]
        hold = parsed["hold"]
        internal_clean = (setup["internal"]["violations"] or 0) == 0 and (hold["internal"]["violations"] or 0) == 0
        status = "passed" if setup["violations"] == 0 and hold["violations"] == 0 else "failed"
        artifacts = {
            "sta_tcl": str(work / "sta.tcl"),
            "sta_log": str(work / "sta.log"),
            "odb": odb,
            "sdc": sdc,
        }
        if resolved.get("spef"):
            artifacts["spef"] = resolved["spef"]
        return ToolResult(
            result={
                "status": status,
                "corner": str(ctx.inputs.get("corner") or "TC").upper(),
                "cell_vt": cell_vt,
                "setup": setup,
                "hold": hold,
                "internal_clean": internal_clean,
                **trust_metadata(
                    source="tool", tool="openroad-sta", tool_available=True,
                    command=cmd, artifacts=artifacts,
                ),
            },
            issues=[] if status == "passed" else ["Post-route STA has timing violations"],
        )
