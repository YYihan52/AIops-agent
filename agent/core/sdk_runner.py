"""对 Claude Agent SDK `query()` 的薄封装，统一加上成本/超时控制。

作用：把 SDK 那套「异步迭代消息流」的细节挡在这里，对上层只返回一个统一的 dict：
  {raw, structured, degraded, usage, cost_usd, num_turns}
这样两个 Agent 的代码就不用各自去处理 SDK 的消息循环了。

一个关键约定：本函数遇到 Agent 失败绝不抛异常，而是返回带 degraded 标记的 dict，
让上层的逃生口去降级——这是「流程永不崩溃」理念在底座这一层的落地。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    query,
)

from agent import config


def _accumulate_usage(dst: dict[str, int], usage: Optional[dict]) -> None:
    """把一条消息里的 token 用量累加进 dst（同一趟 query 可能有多条 usage）。"""
    if not usage:
        return
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        v = usage.get(k)
        if isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + int(v)


async def run_sdk(
    prompt: str,
    *,
    cwd: Optional[str],
    allowed_tools: list[str],
    append_prompt: str,
    hook,
    max_turns: int,
    timeout_s: int,
    json_schema: Optional[dict] = None,
    max_budget_usd: Optional[float] = None,
    mcp_servers: Optional[dict] = None,
    skills: Optional[Any] = None,
) -> dict[str, Any]:
    """跑一趟 Agent。遇到 Agent 失败不抛异常，而是返回带 degraded 标记的 dict。"""
    # wall-clock 计时：给评测 MTTR 用；即使 degraded 也要有 latency，所以放最外层
    t0 = time.perf_counter()
    # 隔离：不继承宿主机 CLI 的记忆/设置，保证每次运行干净可复现
    os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

    # setting_sources：默认 [] 完全隔离。需要加载 Skill 时才放开到 "project" 作用域，
    # 让 SDK 能发现 .claude/skills/（故障诊断处置 Agent 的 cwd 正是仓库根）。mcp_servers 与它互不相关。
    setting_sources = ["project"] if skills is not None else []

    options_kwargs: dict[str, Any] = dict(
        cwd=cwd,
        model=config.MODEL,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": append_prompt,
        },
        allowed_tools=allowed_tools,
        permission_mode="acceptEdits",
        disallowed_tools=["Bash(rm -rf *)", "Bash(git push --force *)"],
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[hook])]},
        setting_sources=setting_sources,
        max_turns=max_turns,
    )
    if json_schema is not None:
        options_kwargs["output_format"] = {"type": "json_schema", "schema": json_schema}
    if max_budget_usd is not None:
        options_kwargs["max_budget_usd"] = max_budget_usd
    if mcp_servers is not None:
        options_kwargs["mcp_servers"] = mcp_servers
    if skills is not None:
        options_kwargs["skills"] = skills

    opts = ClaudeAgentOptions(**options_kwargs)

    result_text: Optional[str] = None
    structured: Any = None
    usage: dict[str, int] = {}
    cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    degraded: Optional[str] = None

    try:
        async with asyncio.timeout(timeout_s):
            async for msg in query(prompt=prompt, options=opts):
                if isinstance(msg, ResultMessage):
                    _accumulate_usage(usage, msg.usage)
                    if msg.total_cost_usd is not None:
                        cost_usd = msg.total_cost_usd
                    num_turns = msg.num_turns
                    if msg.subtype == "success":
                        result_text = msg.result
                        structured = msg.structured_output
                    else:
                        # 各种非成功收尾：轮数用尽 / 执行出错 / 超预算 / 重试耗尽
                        degraded = msg.subtype
                        result_text = msg.result
                        structured = msg.structured_output
    except asyncio.TimeoutError:
        degraded = "timeout"
    except Exception as exc:  # noqa: BLE001
        # 注意：命中 max_turns 这类硬上限时，SDK 是「抛异常」而不是 yield 一条错误消息。
        # 这里统一 catch 转成 degraded 结果，让上层逃生口降级人工，而不是让整个流程崩掉。
        msg = str(exc)
        if "maximum number of turns" in msg:
            degraded = "error_max_turns"
        elif "budget" in msg.lower():
            degraded = "error_max_budget"
        else:
            degraded = f"sdk_error: {msg[:200]}"

    return {
        "raw": result_text,
        "structured": structured,
        "degraded": degraded,
        "usage": usage,
        "cost_usd": cost_usd,
        "num_turns": num_turns,
        "latency_s": time.perf_counter() - t0,
    }
