"""多轮效果评测驱动器：把 expected.json 里每个场景跑 N 轮，落盘一份扁平 JSONL。

只关心效果指标（Outcome Metrics）。每次调用 `agent.run.run(alert)` 拿到一份完整 report，
再用 `eval.metrics.extract()` 压平成一行 metric dict 追加到 JSONL。汇总由后续的
`eval.aggregate` 负责。

关键设计：
- **默认 N 由 CLI 指定，单场景可用 `expected.json` 里的 `runs` 字段覆盖**（例如 s4 涉及真代码 PR，
  单场景 `runs=1` 就够，不要重复烧钱）。
- **每次单独 run 之前显式清一次去重指纹表**（`agent.integrations.ingest._SEEN`），
  否则同一 scenario 的第 2、3 轮会被 30 分钟 TTL 判为重复告警，直接走 `skipped_duplicate`，
  评测数据全废。
- **`depends_on` 场景（当前只有 s5_dup）**：需要**在同一进程内先跑一次依赖场景（s2）留下指纹，
  再紧跟着跑本场景**，这样才能触发去重路由。这里的顺序保证靠代码本身，不依赖外部脚本。

**产出按「评测对象」分两层落盘**：`<reports-dir>/<模型名>_<日期>/run_<ts>/`。
外层 `<模型名>_<日期>/` 对应「这次是用哪个模型、哪天测的」（比如换成 Claude 5 Opus
测一遍，就会另开一个 `opus_20260907/`，跟别的模型/别的日期互不干扰）；内层每个
`run_<ts>/` 是当次调用产出的 jsonl + 专属 metrics.json；外层目录下还会自动维护一份
合并了该模型当天所有 run 的 `metrics.json`，作为这个模型这一天的完整基线大盘。

用法：
    python3 -m eval.multi_run                       # 全场景，默认 3 轮 / 场景
    python3 -m eval.multi_run --runs 5              # 全场景，默认 5 轮 / 场景
    python3 -m eval.multi_run --only s2 s4 s_lowconf
    python3 -m eval.multi_run --reports-dir reports/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import config
from agent.integrations import ingest
from agent.integrations.ingest import load_alert
from agent.run import run
from eval import metrics
from eval.aggregate import build_report, write_report

EXPECTED_PATH = Path(__file__).parent / "expected.json"


def _model_slug() -> str:
    """把 config.MODEL 转成能安全当目录名的字符串（斜杠/点号等换成短横线）。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", config.MODEL)


def _reset_dedup_cache() -> None:
    """清空进程内去重指纹表，让下一次 run() 一定会真的走完整流程。"""
    ingest._SEEN.clear()


async def _run_once(alert_path: str) -> dict:
    """加载告警 → 跑一次编排流程 → 返回完整 report。"""
    alert = load_alert(alert_path)
    return await run(alert)


async def _run_scenario(name: str, exp: dict, exp_all: dict, default_runs: int) -> list[dict]:
    """跑单个 scenario 的 N 轮，返回 N 条扁平 metric row。

    - 有 `depends_on` 的场景：每一轮都先跑一次依赖场景（清缓存 → 跑 dep → 立刻跑本场景），
      这样本场景才能命中同一进程内 dep 留下的指纹。
    - 没有 `depends_on` 的场景：每一轮前清一次缓存，保证不被上一轮自己的指纹判重。
    """
    effective_runs = int(exp.get("runs", default_runs))
    depends_on = exp.get("depends_on")
    rows: list[dict] = []

    for i in range(effective_runs):
        _reset_dedup_cache()

        if depends_on:
            dep_exp = exp_all[depends_on]
            print(f"[multi_run] {name} run#{i}: seeding dependency {depends_on} ({dep_exp['alert']})")
            _ = await _run_once(dep_exp["alert"])
            # 不清缓存，紧跟着跑本场景以命中指纹去重
            report = await _run_once(exp["alert"])
        else:
            report = await _run_once(exp["alert"])

        row = metrics.extract(name, exp, report)
        row["run_index"] = i
        row["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows.append(row)

        mark = (
            f"route={row['route']}  service_ok={row['service_ok']}  "
            f"kind_ok={row['kind_ok']}  route_ok={row['route_ok']}  "
            f"total_latency_s={row['total_latency_s']}"
        )
        print(f"[multi_run] {name} run#{i}: {mark}")

    return rows


async def _main(args: argparse.Namespace) -> None:
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    names = args.only or list(expected.keys())

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    date_str = ts[:8]
    # 外层按「模型_日期」分目录，内层每次调用独立开一个 run_<ts>/ 子目录：
    # 这次的 jsonl 不会跟别的批次混在一起被 aggregate 一起汇总，
    # 这次生成的 metrics.json 也不会覆盖掉别的批次已经落盘的 metrics.json。
    model_dir = Path(args.reports_dir) / f"{_model_slug()}_{date_str}"
    run_dir = model_dir / f"run_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / f"multi_run_{ts}.jsonl"

    t_start = time.perf_counter()
    total_rows = 0
    with out_path.open("w", encoding="utf-8") as f:
        for name in names:
            if name not in expected:
                print(f"[multi_run] WARN: {name} not in expected.json, skipped")
                continue
            exp = expected[name]
            print(f"\n[multi_run] === {name} (runs={exp.get('runs', args.runs)}) ===")
            rows = await _run_scenario(name, exp, expected, args.runs)
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
            total_rows += len(rows)

    elapsed = time.perf_counter() - t_start
    print(f"\n[multi_run] wrote {total_rows} rows to {out_path} in {elapsed:.1f}s")

    if total_rows:
        run_metrics_path = run_dir / "metrics.json"
        write_report(build_report([out_path]), run_metrics_path)
        print(f"[multi_run] wrote metrics to {run_metrics_path}")

        # 同时刷新「这个模型这一天」的合并大盘：汇总该 model_dir 下所有 run_*/ 的 jsonl。
        model_jsonl = sorted(model_dir.glob("run_*/multi_run_*.jsonl"))
        model_metrics_path = model_dir / "metrics.json"
        write_report(build_report(model_jsonl), model_metrics_path)
        print(f"[multi_run] wrote combined metrics to {model_metrics_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AIOps agent multi-run effectiveness eval")
    parser.add_argument("--runs", type=int, default=3, help="default runs per scenario (可被 expected.json 中的 runs 字段覆盖)")
    parser.add_argument("--only", nargs="*", help="subset of scenario names, e.g. --only s2 s4")
    parser.add_argument("--reports-dir", default="reports", help="output directory for jsonl")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
