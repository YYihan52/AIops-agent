"""效果指标聚合器：把 `multi_run_*.jsonl` 压成一份 `reports/metrics.json`。

只做 7 条效果指标 + 1 个 MTTR 行业基线对照数：

    1. service_accuracy       故障服务定位准确率  = mean(service_ok)
    2. kind_accuracy          故障类型识别准确率  = mean(kind_ok)
    3. route_accuracy         路由决策准确率      = mean(route_ok)
    4. fix_success_rate       代码修复成功率      = sum(fix_verified) / sum(triggered_code_fix)
    5. code_location_accuracy 问题代码定位准确率  = sum(changed_files_hit) / sum(changed_files_hit is not None)
    6. mttr_seconds_p50 / mttr_minutes_p50   平均排查+修复耗时的 p50
    7. pr_submission_rate     PR 提交成功率      = sum(pr_created) / sum(fix_verified)

**分母口径**：
- 指标 4/7 的分母只算真的触发了 code_fix Agent 的场景（`triggered_code_fix=True`）；
  没触发 code_fix 的场景（例如 s1/s3/s6_info/s_lowconf/s5_dup）不进入分母，避免"没修当然没修好"的稀释。
- 指标 5 的分母只算 `changed_files_hit is not None` 的行（即 expected.json 里显式声明了
  `expect_changed_files_any_of` 的场景），其它场景没有 gold set，不参与分母。
- 指标 6 用 p50 中位数而不是 mean，避免 s4/s7 之类真代码修复场景的重尾把平均数拉爆。

用法：
    python3 -m eval.aggregate                                # 汇总 reports/multi_run_*.jsonl
    python3 -m eval.aggregate --input reports/multi_run_20260817T090000.jsonl
    python3 -m eval.aggregate --output reports/metrics.json  # 自定义输出路径
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports"


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except FileNotFoundError:
        return None
    return None


def _load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    return rows


def _safe_div(numer: float, denom: float) -> float | None:
    if denom <= 0:
        return None
    return round(numer / denom, 4)


def _p50(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 3)


def _aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {
            "rows": 0,
            "scenarios_total": 0,
            "runs_per_scenario_avg": 0,
            "effectiveness": {},
        }

    # 分组以便报"每场景样本量"
    per_scenario: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        per_scenario[r["scenario"]].append(r)

    scenarios_total = len(per_scenario)
    runs_per_scenario_avg = round(len(rows) / scenarios_total, 2)

    # 指标 1/2/3：每行都可计算，直接 mean
    service_acc = _safe_div(sum(1 for r in rows if r["service_ok"]), len(rows))
    kind_acc = _safe_div(sum(1 for r in rows if r["kind_ok"]), len(rows))
    route_acc = _safe_div(sum(1 for r in rows if r["route_ok"]), len(rows))

    # 指标 4：只在触发 code_fix 的行内算
    fix_rows = [r for r in rows if r["triggered_code_fix"]]
    fix_success = _safe_div(
        sum(1 for r in fix_rows if r.get("fix_verified")),
        len(fix_rows),
    )

    # 指标 5：只在显式声明 gold set 的行内算（changed_files_hit is not None）
    loc_rows = [r for r in rows if r.get("changed_files_hit") is not None]
    code_location_acc = _safe_div(
        sum(1 for r in loc_rows if r["changed_files_hit"]),
        len(loc_rows),
    )

    # 指标 6：所有行的 total_latency_s 取 p50
    latencies = [float(r["total_latency_s"]) for r in rows if r.get("total_latency_s")]
    mttr_s_p50 = _p50(latencies)
    mttr_min_p50 = round(mttr_s_p50 / 60.0, 3) if mttr_s_p50 is not None else None

    # 指标 7：分母是 fix_verified=True 的行，分子是同时 pr_created=True 的行
    verified_rows = [r for r in fix_rows if r.get("fix_verified")]
    pr_rate = _safe_div(
        sum(1 for r in verified_rows if r.get("pr_created")),
        len(verified_rows),
    )

    return {
        "rows": len(rows),
        "scenarios_total": scenarios_total,
        "runs_per_scenario_avg": runs_per_scenario_avg,
        "effectiveness": {
            "service_accuracy": service_acc,
            "kind_accuracy": kind_acc,
            "route_accuracy": route_acc,
            "fix_success_rate": fix_success,
            "code_location_accuracy": code_location_acc,
            "mttr_seconds_p50": mttr_s_p50,
            "mttr_minutes_p50": mttr_min_p50,
            "pr_submission_rate": pr_rate,
        },
        "denominators": {
            "service_accuracy": len(rows),
            "kind_accuracy": len(rows),
            "route_accuracy": len(rows),
            "fix_success_rate": len(fix_rows),
            "code_location_accuracy": len(loc_rows),
            "mttr_p50": len(latencies),
            "pr_submission_rate": len(verified_rows),
        },
        "per_scenario_runs": {name: len(rs) for name, rs in per_scenario.items()},
    }


def _main(args: argparse.Namespace) -> None:
    reports_dir = Path(args.reports_dir)
    if args.input:
        paths = [Path(args.input)]
    else:
        paths = sorted(reports_dir.glob("multi_run_*.jsonl"))

    if not paths:
        raise SystemExit(
            f"[aggregate] no multi_run_*.jsonl found in {reports_dir}; "
            "run `python3 -m eval.multi_run` first."
        )

    print(f"[aggregate] loading {len(paths)} file(s):")
    for p in paths:
        print(f"  - {p}")

    rows = _load_rows(paths)
    agg = _aggregate(rows)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _git_head(),
        "sources": [str(p) for p in paths],
        **agg,
        "baseline_reference": {
            "human_mttr_minutes_low": 30,
            "human_mttr_minutes_high": 60,
            "source": "Google SRE Book / DORA Report 行业中位数",
            "note": "引用行业公开均值做对照，非本项目内做过的人工对照实验。",
        },
    }

    out_path = Path(args.output) if args.output else reports_dir / "metrics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n[aggregate] wrote {out_path}")

    eff = agg["effectiveness"]
    print("[aggregate] effectiveness summary:")
    for k, v in eff.items():
        print(f"  {k:28s} = {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate AIOps agent effectiveness metrics")
    parser.add_argument("--input", help="single multi_run_*.jsonl file (default: glob all in reports-dir)")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR), help="reports directory")
    parser.add_argument("--output", help="output path (default: <reports-dir>/metrics.json)")
    args = parser.parse_args()
    _main(args)


if __name__ == "__main__":
    main()
