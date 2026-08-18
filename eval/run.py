"""轻量 eval：对每个场景跑一遍编排流程，把结果和 expected.json 里的期望比对，并记录成本。

检查项：诊断是否正确、路由是否正确（含低置信度降级）、s4 修复是否生效、以及成本/降级指标。

  python3 -m eval.run                 # 跑 expected.json 里的全部场景
  python3 -m eval.run --only s2 s4    # 只跑指定子集
  python3 -m eval.run --dry-run       # 只校验 expected.json + 告警文件是否齐全（不真正调用 Agent）
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent import config
from agent.integrations.ingest import load_alert
from agent.run import run

EXPECTED_PATH = Path(__file__).parent / "expected.json"


def _check(name: str, exp: dict, report: dict) -> dict[str, Any]:
    diag = report.get("diagnosis") or {}
    fix = report.get("fix") or {}
    results: dict[str, bool] = {}

    if "kind" in exp:
        results["kind"] = diag.get("kind") == exp["kind"]
    if "kind_any_of" in exp:
        results["kind"] = diag.get("kind") in exp["kind_any_of"]
    if "suspect_service_contains" in exp:
        results["suspect_service"] = exp["suspect_service_contains"].lower() in (
            diag.get("suspect_service", "").lower()
        )
    if "remediation_type" in exp:
        results["remediation_type"] = diag.get("remediation_type") == exp["remediation_type"]
    if exp.get("expect_low_confidence"):
        results["low_confidence"] = diag.get("confidence", 1.0) < config.CONFIDENCE_THRESHOLD
    if "expect_route" in exp:
        results["route"] = report.get("route") == exp["expect_route"]
    if "expect_route_any_of" in exp:
        results["route"] = report.get("route") in exp["expect_route_any_of"]
    if exp.get("expect_fix_verified"):
        results["fix_verified"] = bool(fix.get("verified"))

    meta = report.get("meta", {})
    cost = {
        "route": report.get("route"),
        "confidence": diag.get("confidence"),
        "attempts": meta.get("attempts"),
        "usage": meta.get("usage"),
        "cost_usd": meta.get("cost_usd"),
        "degraded": meta.get("degraded"),
    }
    return {"checks": results, "passed": all(results.values()) if results else False, "cost": cost}


async def _run_one(name: str, exp: dict) -> dict[str, Any]:
    alert = load_alert(exp["alert"])
    report = await run(alert)
    return _check(name, exp, report)


async def _main(args: argparse.Namespace) -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    names = args.only or list(expected.keys())

    if args.dry_run:
        print("[eval] dry-run: validating expected.json + alert files")
        ok = True
        for name in names:
            exp = expected[name]
            p = Path(exp["alert"])
            status = "OK" if p.exists() else "MISSING"
            if not p.exists():
                ok = False
            print(f"  {name}: alert={exp['alert']} [{status}] route_exp={exp.get('expect_route')}")
        print("[eval] dry-run", "PASSED" if ok else "FAILED")
        return

    summary = []
    for name in names:
        print(f"\n[eval] === {name} ===")
        res = await _run_one(name, expected[name])
        mark = "PASS" if res["passed"] else "FAIL"
        print(f"[eval] {name}: {mark}  checks={res['checks']}")
        print(f"[eval] {name}: cost={res['cost']}")
        summary.append((name, res["passed"]))

    print("\n[eval] ===== SUMMARY =====")
    for name, passed in summary:
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")
    total = sum(1 for _, p in summary if p)
    print(f"[eval] {total}/{len(summary)} passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="AIOps agent eval")
    parser.add_argument("--only", nargs="*", help="subset of scenario names")
    parser.add_argument("--dry-run", action="store_true", help="validate fixtures without calling agents")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
