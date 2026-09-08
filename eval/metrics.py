"""效果指标抽取器：把 agent.run.run() 的报告 dict 压平成 7 条效果指标涉及的字段。

只做效果指标（Outcome Metrics），不抽任何过程指标（cost/attempts/usage/degraded/...）。

单条 report → 单条 metric row：
    {
      "scenario": "s4",
      "route": "code_fix_pr",
      "service_ok": True/False,          # 指标 1：故障服务定位
      "kind_ok": True/False,             # 指标 2：故障类型识别
      "route_ok": True/False,            # 指标 3：路由决策
      "triggered_code_fix": True/False,  # 用于指标 4/5/7 的分母
      "fix_verified": True/False/None,   # 指标 4：代码修复成功
      "changed_files_hit": True/False/None,  # 指标 5：问题代码定位（交集判断）
      "diag_latency_s": float,           # 指标 6：诊断阶段耗时
      "fix_latency_s": float | None,     # 指标 6：修复阶段耗时
      "total_latency_s": float,          # 指标 6：整体 wall-clock
      "pr_created": True/False/None,     # 指标 7：PR 提交
    }
"""
from __future__ import annotations

from typing import Any


def _service_ok(exp: dict, diag: dict) -> bool:
    suspect = (diag.get("suspect_service", "") or "").lower()
    if "suspect_service_contains_any_of" in exp:
        return any(n.lower() in suspect for n in exp["suspect_service_contains_any_of"])
    needle = exp.get("suspect_service_contains")
    if not needle:
        return True
    return needle.lower() in suspect


def _kind_ok(exp: dict, diag: dict) -> bool:
    if "kind" in exp:
        return diag.get("kind") == exp["kind"]
    if "kind_any_of" in exp:
        return diag.get("kind") in exp["kind_any_of"]
    return True


def _route_ok(exp: dict, route: str | None) -> bool:
    if "expect_route" in exp:
        return route == exp["expect_route"]
    if "expect_route_any_of" in exp:
        return route in exp["expect_route_any_of"]
    return True


def _changed_files_hit(exp: dict, fix: dict | None) -> bool | None:
    gold = exp.get("expect_changed_files_any_of")
    if not gold:
        return None
    if not fix:
        return False
    changed = fix.get("changed_files") or []
    if not changed:
        return False
    gold_set = {g.lower() for g in gold}
    for cf in changed:
        cf_l = (cf or "").lower()
        for g in gold_set:
            if g in cf_l or cf_l.endswith(g) or g.endswith(cf_l):
                return True
    return False


def extract(scenario: str, exp: dict, report: dict) -> dict[str, Any]:
    """把一次 run() 的 report + 该场景的 expected 期望，压平成一行 metric dict。"""
    diag = report.get("diagnosis") or {}
    fix = report.get("fix")
    meta = report.get("meta") or {}
    fix_meta = meta.get("fix_meta") or {}

    diag_latency = float(meta.get("latency_s") or 0.0)
    fix_latency = float(fix_meta.get("latency_s")) if fix_meta.get("latency_s") is not None else None
    total_latency = diag_latency + (fix_latency or 0.0)

    triggered_code_fix = fix is not None
    fix_verified: bool | None = None
    pr_created: bool | None = None
    if triggered_code_fix:
        fix_verified = bool(fix.get("verified"))
        pr_created = bool(fix.get("pr_url"))

    return {
        "scenario": scenario,
        "route": report.get("route"),
        "service_ok": _service_ok(exp, diag),
        "kind_ok": _kind_ok(exp, diag),
        "route_ok": _route_ok(exp, report.get("route")),
        "triggered_code_fix": triggered_code_fix,
        "fix_verified": fix_verified,
        "changed_files_hit": _changed_files_hit(exp, fix),
        "diag_latency_s": round(diag_latency, 3),
        "fix_latency_s": round(fix_latency, 3) if fix_latency is not None else None,
        "total_latency_s": round(total_latency, 3),
        "pr_created": pr_created,
    }
