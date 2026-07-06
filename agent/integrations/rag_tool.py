"""Agentic RAG：一个 in-process MCP 工具，让 故障诊断处置 Agent 能检索历史事件工单。

这正是记忆「agentic」的关键——不是由编排层把历史强行塞进提示词，而是让 故障诊断处置 Agent 在
排查过程中自己决定「要不要检索、检索什么」，通过一次工具调用去查。底层是 memory.py（Milvus + BGE）。

这个工具是只读的（只查 Milvus），所以不会破坏 故障诊断处置 Agent 的只读诊断边界。
它只挂在 故障诊断处置 Agent 上（见 agent/agents/diagnose.py）。
"""
from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent import config
from agent.integrations import memory


def _format_hits(hits: list[dict]) -> str:
    if not hits:
        return "没有检索到相似的历史工单（可能是首次遇到此类问题，或历史库为空）。"
    lines = ["检索到以下历史相似诊断工单（按相似度降序）：\n"]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"[{i}] 相似度={h.get('score')} | 服务={h.get('suspect_service')} | "
            f"类型={h.get('kind')} | 当时走向={h.get('route')}\n"
            f"    根因摘要: {h.get('summary')}\n"
            f"    处置: {h.get('remediation_detail')}"
        )
    lines.append(
        "\n注意：历史工单仅供参考，请结合当前实时观测证据独立判断，不要盲目照搬。"
    )
    return "\n".join(lines)


@tool(
    "search_past_incidents",
    "语义检索历史相似的诊断工单（根因/处置经验）。当你想参考过去是否处理过类似故障、"
    "或想借鉴历史根因与处置方式时调用。传入描述当前故障的自然语言查询（如告警症状、"
    "可疑服务、现象），返回最相似的若干历史工单摘要。",
    {"query": str, "top_k": int},
)
async def search_past_incidents(args: dict) -> dict:
    query = (args or {}).get("query", "")
    top_k = (args or {}).get("top_k") or config.RAG_TOP_K
    if not query:
        return {"content": [{"type": "text", "text": "查询为空，请提供描述当前故障的查询文本。"}],
                "is_error": True}
    hits = memory.search_tickets(query, top_k=top_k)
    return {"content": [{"type": "text", "text": _format_hits(hits)}]}


# in-process MCP server。挂载时用的字典 KEY（见 diagnose.py 里的
# mcp_servers={"aiops_rag": server}）会拼成工具全名
# `mcp__aiops_rag__search_past_incidents`——这个 key 要保持稳定，别乱改。
server = create_sdk_mcp_server(
    name="aiops_rag",
    version="1.0.0",
    tools=[search_past_incidents],
)

TOOL_NAME = "mcp__aiops_rag__search_past_incidents"
