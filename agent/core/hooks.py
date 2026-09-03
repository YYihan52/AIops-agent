"""PreToolUse hook：在 Bash 命令执行前做纵深防御的「硬拦截 / 硬放行」。

提示词里写「你只能做这些」只是软约束——模型可能不听。这个 hook 才是**硬保证**：
它和 allowed/disallowed 工具清单一起兜底。一旦命中规则，hook 的决定压倒一切。

诊断处置 Agent 的边界（本文件的重点）：
它不再「纯只读」，而是「只读 + 一份**可枚举的低风险线上操作白名单**」。判定顺序：
  1. 命中低风险白名单（且目标在授权清单内）→ **显式放行 allow**，并记进执行台账；
  2. 命中任何写/变更/高风险命令 → **拦截 deny**（redis/db/删除/改集群/重启有状态组件…）；
  3. 其余（只读查询）→ 放行。
「哪些操作能自动执行」这件事完全由 agent/core/remediation.py 的白名单枚举决定，不靠提示词。

修复 Agent 的边界不变：可改代码，但绝不能碰线上基础设施 / 强推 / 推 master。
"""
from __future__ import annotations

import re
from typing import Any

from agent.core import remediation

# 诊断处置 Agent 禁止的「写/变更」类命令（白名单外的一切写操作，含高风险线上操作）。
# 注意：kubectl rollout restart / delete pod / set resources（k8s 后端）与
# docker restart/update/compose up（docker 后端）这类**可能**是白名单低风险操作，所以不在这里
# 粗暴拦死——先由 remediation.classify_command 判定，命中白名单才放行；没命中（形态不合法/目标越权）
# 就落到这里的 kubectl/docker 规则被兜底拦下，不留灰色地带。
_READONLY_DENY = [
    # kubectl 一切写/变更/进容器动作。whitelist 只精确放行 rollout restart / delete pod / set resources 三种
    # 合法形态；任何变体（scale 缩容、exec 进容器、apply/patch 改集群…）都在这里被拦。
    r"\bkubectl\s+(apply|delete|scale|edit|patch|create|replace|rollout\s+undo|rollout\s+restart|cordon|drain|set|exec|cp|attach|port-forward)\b",
    r"\bhelm\s+(install|upgrade|uninstall|rollback|delete)\b",
    # docker：rm/stop/kill/rmi/exec/run 一律禁（删除资源、进容器改状态都属高风险）。
    # restart/update/compose 交给白名单先判，未命中白名单则由 _WRITE_FALLBACK_DENY 兜底拦下。
    r"\bdocker\s+(rm|stop|kill|rmi|exec|run)\b",
    r"\bgit\s+(push|commit|merge|reset|rebase|checkout\s+-b|tag|clone)\b",
    r"\bgit\s+branch\s+(-[dDmM]\b|(?!-)\S)",  # git branch 只拦创建/删除/改名；纯查看（-a/-v/--list/无参数）放行
    r"\brm\s+-rf\b",
    r">\s*(?!/dev/(?:null|stdout|stderr)\b)/",  # 拦截重定向写文件；放过 2>/dev/null 这类丢弃输出的读命令
    # 高风险数据面/控制面操作：redis/db/删除，绝不自动执行（即便配了白名单也不放）。
    r"\bredis-cli\b",
    r"\b(mysql|psql|mongo|mongosh)\b",
    r"\bflushall\b|\bflushdb\b|\bdrop\s+(table|database)\b|\bdelete\s+from\b|\btruncate\b",
]

# 白名单没接住、但明显是「变更类 docker/compose」的命令，兜底拦下（比如 docker update 打了非法目标、
# docker compose down 等）。这样「变更类命令」要么命中白名单被放行，要么一律被拦，不留灰色地带。
# （kubectl 的变更动词已在 _READONLY_DENY 里覆盖，无需在此重复。）
_WRITE_FALLBACK_DENY = [
    r"\bdocker\s+(restart|update|pause|unpause)\b",
    r"\bdocker(?:\s+compose|-compose)\b(?![.*])",  # 排除 docker-compose.yml / docker-compose*.yml 这类文件名/glob，只拦命令调用
]

# 两个 Agent 都禁止的「线上基础设施变更」类命令（代码修复 Agent 能改码，但一样不许动线上）
_ONLINE_OP_DENY = [
    r"\bkubectl\s+(apply|delete|scale|edit|patch|rollout\s+undo|rollout\s+restart|cordon|drain|set|exec|cp|attach|port-forward)\b",
    r"\bhelm\s+(install|upgrade|uninstall|rollback|delete)\b",
    r"\bdocker\s+(rm|stop|restart|kill|rmi|update)\b",
    r"\bdocker(?:\s+compose|-compose)\s+(up|down|restart|stop)\b",
    r"\brm\s+-rf\b",
    r"\bgit\s+push\s+.*--force\b|\bgit\s+push\s+.*\bmaster\b|\bgit\s+push\s+.*\bmain\b",
]


def _extract_command(input_data: dict[str, Any]) -> str:
    if input_data.get("tool_name") != "Bash":
        return ""
    return str(input_data.get("tool_input", {}).get("command", ""))


def _deny(input_data: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": input_data.get("hook_event_name", "PreToolUse"),
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow(input_data: dict[str, Any], reason: str) -> dict[str, Any]:
    """显式放行（用于白名单内的低风险操作）。allow 会跳过后续权限询问直接执行。"""
    return {
        "hookSpecificOutput": {
            "hookEventName": input_data.get("hook_event_name", "PreToolUse"),
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }


def _match(command: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        if re.search(pat, command):
            return pat
    return None


async def guard_diagnose_ops(input_data, tool_use_id, context):  # noqa: ANN001
    """故障诊断处置 Agent 专用：只读 + 低风险操作白名单。

    判定顺序（第一命中即返回）：
    1. 命中低风险白名单且目标授权 → **allow**，并在执行台账里登记（产出记录的可信来源）。
    2. 命中低风险白名单但目标未授权（如想动 kafka/db）→ **deny**。
    3. 命中任何写/变更/高风险命令 → **deny**。
    4. 变更类 docker 命令没被白名单接住 → **deny**（兜底，杜绝灰色地带）。
    5. 其余（只读查询）→ 放行。
    """
    command = _extract_command(input_data)
    if not command:
        return {}

    verdict = remediation.classify_command(command)
    if verdict["decision"] == "allow":
        remediation.record_execution(verdict["action"], verdict["target"], command.strip())
        return _allow(
            input_data,
            f"低风险处置已授权自动执行：{verdict['action']} → {verdict['target']}。",
        )
    if verdict["decision"] == "deny":
        return _deny(input_data, verdict["reason"])

    hit = _match(command, _READONLY_DENY)
    if hit:
        return _deny(
            input_data,
            f"诊断处置 Agent 禁止此写/变更命令（命中: {hit}）——高风险操作只发飞书卡片交人工。",
        )
    hit = _match(command, _WRITE_FALLBACK_DENY)
    if hit:
        return _deny(
            input_data,
            f"变更类命令不在低风险白名单内（命中: {hit}），拒绝执行。可自动执行的操作见白名单。",
        )
    return {}


async def deny_online_ops(input_data, tool_use_id, context):  # noqa: ANN001
    """代码修复 Agent 专用：可以改代码，但绝不能变更线上基础设施 / 强推 / 推 master。"""
    command = _extract_command(input_data)
    if not command:
        return {}
    hit = _match(command, _ONLINE_OP_DENY)
    if hit:
        return _deny(
            input_data,
            f"修复 Agent 禁止线上变更/强推/推 master（命中: {hit}）。只能改代码 + 推特性分支 + 提 PR。",
        )
    return {}
