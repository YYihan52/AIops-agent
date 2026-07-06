"""故障诊断处置 Agent：只读排查告警 + 结构化输出校验 + 重试 + 兜底降级。

设计要点：整条路由都押在 故障诊断处置 Agent 产出的这份 Diagnosis 上，所以必须保证它一定是
一份「合法」的结构化结果。做法是——强制 JSON schema 校验，失败就把错误喂回去重试；
反复失败就合成一个 confidence=0 的兜底结果，让编排层的低置信度逃生口把它交给人工。
这样无论模型怎么抽风，主流程永远不会崩。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from agent import config
from agent.agents import prompts
from agent.core import remediation
from agent.core.hooks import guard_diagnose_ops
from agent.core.schema import DIAGNOSIS_JSON_SCHEMA, Diagnosis, ExecutedAction
from agent.core.sdk_runner import run_sdk
from agent.integrations import rag_tool


def _coerce(structured: Any, raw: Optional[str]) -> Optional[Diagnosis]:
    """把 SDK 返回的结构化对象（或原始文本兜底）校验成 Diagnosis，失败返回 None。"""
    candidate = structured
    if candidate is None and raw:
        text = raw.strip()
        # 容错：模型偶尔会用 ```json 代码块把 JSON 包起来，这里剥掉
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") : text.rfind("}") + 1]
        try:
            candidate = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
    if candidate is None:
        return None
    try:
        return Diagnosis.model_validate(candidate)
    except ValidationError:
        return None


async def diagnose(alert: dict) -> dict[str, Any]:
    """运行 故障诊断处置 Agent，返回 {diagnosis: Diagnosis, meta: {成本/降级等}}。"""
    prompt = prompts.diagnose_prompt(alert)
    last_errors = ""
    meta: dict[str, Any] = {"attempts": 0, "usage": {}, "cost_usd": None, "degraded": None}

    # 清空执行台账：本次运行 Agent 自动执行的低风险操作都会被 hook 记进来（跨重试累计）。
    remediation.reset_ledger()

    for attempt in range(config.SCHEMA_RETRY_MAX + 1):
        meta["attempts"] = attempt + 1

        # 诊断处置 Agent 的工具面：只读工具 + Bash。写/变更由 hook 按白名单硬控（见 guard_diagnose_ops）。
        # RAG 开启时再挂上「历史工单检索」MCP 工具。
        allowed = ["Bash", "Read", "Grep", "Glob"]
        mcp_servers = None
        if config.RAG_ENABLED:
            allowed.append(rag_tool.TOOL_NAME)  # 对历史工单做 agentic RAG
            mcp_servers = {"aiops_rag": rag_tool.server}

        out = await run_sdk(
            prompt,
            cwd=str(config.REPO_ROOT),  # 只读访问 deploys.log、.claude/skills/ 等
            allowed_tools=allowed,
            append_prompt=prompts.diagnose_append(),
            hook=guard_diagnose_ops,  # 硬保证：白名单内低风险操作放行，其余写/变更命令拦截
            max_turns=config.DIAGNOSE_MAX_TURNS,
            timeout_s=config.DIAGNOSE_TIMEOUT_S,
            json_schema=DIAGNOSIS_JSON_SCHEMA,
            mcp_servers=mcp_servers,
            skills="all",  # 加载 .claude/skills/ 下的排查手册（会自动放开 setting_sources 到 project）
        )

        # 跨多次重试累计成本/用量
        for k, v in out["usage"].items():
            meta["usage"][k] = meta["usage"].get(k, 0) + v
        if out["cost_usd"] is not None:
            meta["cost_usd"] = (meta["cost_usd"] or 0.0) + out["cost_usd"]
        meta["degraded"] = out["degraded"]

        diag = _coerce(out["structured"], out["raw"])
        if diag is not None:
            _attach_executed_actions(diag)  # 从执行台账回填「真的自动执行了什么」
            return {"diagnosis": diag, "meta": meta}

        # 校验失败 → 把错误原因喂回给模型，让它下一轮修正
        last_errors = _describe_failure(out)
        prompt = prompts.diagnose_retry_prompt(last_errors)

    # 重试用尽 → 返回 confidence=0 的兜底结果，交由编排层逃生口降级人工
    meta["degraded"] = meta["degraded"] or "schema_validation_failed"
    fb = Diagnosis.fallback(f"schema 校验重试 {config.SCHEMA_RETRY_MAX} 次仍失败")
    _attach_executed_actions(fb)  # 即便降级，也如实记录期间已放行执行的低风险操作
    return {"diagnosis": fb, "meta": meta}


def _attach_executed_actions(diag: Diagnosis) -> None:
    """把执行台账（hook 放行低风险操作时记录的）回填进 diagnosis。

    只信任台账，不信任模型自述——这是「产出记录」的可信来源。
    """
    diag.executed_actions = [ExecutedAction(**e) for e in remediation.get_ledger()]


def _describe_failure(out: dict[str, Any]) -> str:
    """把这一轮失败的原因整理成一句话，好让重试提示词能明确告诉模型哪里错了。"""
    if out["degraded"]:
        return f"上次运行降级({out['degraded']})，未产出合法 JSON。"
    raw = (out["raw"] or "")[:500]
    return f"上次输出无法解析为合法 Diagnosis JSON。原始片段: {raw!r}"
