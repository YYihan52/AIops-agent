"""代码修复 Agent（可写）：改码 → 本地 build+test 全过 → 才提 PR。

核心安全设计：把「是否提 PR」死死绑定在「验证是否通过」上。Agent 只能改代码、
推特性分支、提 PR；绝不能动线上、绝不能推 master（由 hook 兜底拦截）。验证不过就
不提 PR，编排层会把它降级成飞书卡片交人工。

本文件还负责：把可疑服务的代码仓 clone 到隔离工作目录，以及把 Agent 的结构化结果解析成 FixResult。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from agent import config
from agent.agents import prompts
from agent.core.hooks import deny_online_ops
from agent.core.schema import FIX_JSON_SCHEMA, FixResult
from agent.core.sdk_runner import run_sdk


def clone_service_repo(service: str, branch: Optional[str] = None) -> Path:
    """把可疑服务的 GitHub 仓 clone 到隔离的 workspace 目录。

    clone 的是「使用者自己的 fork」（config.GITHUB_FORK_OWNER），这样 Agent 后面推特性分支
    时用使用者自己的 token 即可，无需成为上游协作者；随后向上游发起跨仓 PR。
    clone URL 里内嵌 token，Agent 推特性分支时无需再鉴权。
    仓名默认取 config.GITHUB_REPO（当前 MVP 是单仓）。

    `branch`：可选。当前所有场景的 bug 都直接放在上游仓的默认分支（master）上，
    clone 默认分支即可、不需要传。这个参数是「bug 活在某个指定分支」形态的预留
    扩展位：若调用方明确知道目标分支，就显式传入（而不是让 Agent 自己去猜），
    clone 时直接切到那个分支——确定性代码路径，不依赖 LLM 是否忠实转述。
    """
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.WORKSPACE_DIR / config.GITHUB_REPO
    if dest.exists():
        shutil.rmtree(dest)

    if config.GITHUB_TOKEN:
        auth = f"{config.GITHUB_TOKEN}@github.com"
    else:
        auth = "github.com"
    clone_url = f"https://{auth}/{config.GITHUB_FORK_OWNER}/{config.GITHUB_REPO}.git"

    cmd = ["git", "clone", "--depth", "50"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, str(dest)]
    subprocess_run(cmd)
    return dest


def subprocess_run(cmd: list[str]) -> None:
    import subprocess

    subprocess.run(cmd, check=True, capture_output=True, text=True)


def parse_fix(out: dict[str, Any]) -> FixResult:
    """从 Agent 的结构化结果里读出 verified / pr_url / degraded 等，解析失败按未验证处理。"""
    candidate = out.get("structured")
    if candidate is None and out.get("raw"):
        text = out["raw"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") : text.rfind("}") + 1]
        try:
            candidate = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            candidate = None

    if candidate is not None:
        try:
            fr = FixResult.model_validate(candidate)
            if out.get("degraded") and not fr.degraded:
                fr.degraded = out["degraded"]
            return fr
        except ValidationError:
            pass

    # 解析不出来 → 一律当作「未验证 + 降级」，绝不会误判成提了 PR
    return FixResult(
        verified=False,
        degraded=out.get("degraded") or "unparseable_fix_result",
        verify_log=(out.get("raw") or "")[:500],
    )


async def code_fix(rc: dict, cwd: Optional[str] = None, branch: Optional[str] = None) -> dict[str, Any]:
    """在 clone 好的仓里运行 代码修复 Agent。返回 {fix: FixResult, meta: {...}}。

    `branch`：见 `clone_service_repo` 的说明——可选的「bug 在指定分支」扩展位，
    由调用方显式传入；当前无场景使用（所有 bug 都在默认分支 master 上）。
    """
    if cwd is None:
        repo = clone_service_repo(rc.get("suspect_service", config.GITHUB_REPO), branch=branch)
        cwd = str(repo)

    out = await run_sdk(
        prompts.fix_prompt(rc, branch=branch),
        cwd=cwd,
        allowed_tools=["Bash", "Read", "Write", "Edit", "Grep", "Glob"],  # 比 故障诊断处置 Agent 多了写文件权限
        append_prompt=prompts.fix_append(),
        hook=deny_online_ops,  # 硬保证：可以改码，但禁止动线上 / 强推 / 推 master
        max_turns=config.FIX_MAX_TURNS,
        timeout_s=config.FIX_TIMEOUT_S,
        json_schema=FIX_JSON_SCHEMA,
    )
    fix = parse_fix(out)
    meta = {
        "usage": out["usage"],
        "cost_usd": out["cost_usd"],
        "num_turns": out["num_turns"],
        "degraded": out["degraded"],
        "latency_s": out.get("latency_s", 0.0),
    }
    return {"fix": fix, "meta": meta}
