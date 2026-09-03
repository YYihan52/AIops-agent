"""线上处置操作白名单：把「Agent 能自动执行的线上操作」收敛成一份可枚举的有限清单。

这是「让诊断 Agent 从只读升级为能自动止血」的安全底座。核心设计原则：
**不靠提示词约束，靠代码把可执行的操作枚举死。** 提示词只是「告知」Agent 有哪些手段，
真正决定「这条命令能不能跑」的，是这里的白名单正则 + PreToolUse hook（agent/core/hooks.py）。

## 风险分级（这份文件只放「低风险、可自动执行」的）
- **低风险（Agent 可自动执行）**：滚动重启/迁移单个工作负载、临时扩副本、临时抬高资源上限。
  这些操作影响面小、可逆、不碰数据——即使误操作，最坏也就是重启一个无状态实例。
- **高风险（绝不自动执行，维持现状发飞书卡片交人工）**：操作 redis/db、删除资源、
  改集群配置、缩容、批量操作。这类操作**根本不在这份白名单里**，所以 hook 一定拦下。

## 两套后端（k8s 为主，docker 为本地默认）
生产运维基本盘是 Kubernetes，所以白名单**以 k8s 为主形态**——放行的是
`kubectl rollout restart` / `kubectl scale` / `kubectl set resources`。
为了让使用者不起集群也能零门槛跑通，另备一套等价的 **docker** 后端（本地默认）：
`docker restart` / `docker compose up --force-recreate` / `docker update`。
由 `config.REMEDIATION_BACKEND`（环境变量 `AIOPS_BACKEND`，默认 `docker`）选择，
两套语义一一对应，代码其余部分（hook / 台账 / 路由）对后端无感知。

## 为什么用「锚定正则 + 目标白名单」而不是「关键字匹配」
每条低风险操作都写成一条**从头到尾锚定（^…$）**的正则，且把操作目标限定在
`ALLOWED_TARGETS` 里。锚定是关键的安全技巧：一旦命令里出现 `&&`、`;`、`|`、`$(...)`、
反引号等命令拼接/注入，整条命令就无法完整匹配 `^…$`，直接落到「不放行」——
从而杜绝「kubectl rollout restart deploy/ad && rm -rf /」这类夹带私货的绕过。
连命令里的 flag 取值（命名空间、资源上限）都限定在安全字符集内，堵死
「-n foo;curl …」这种把注入藏进 flag 参数的绕过。
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from agent import config


# 允许被自动处置的目标实例（有限集合）。只放 demo 栈里无状态、可安全重启/扩容的业务容器。
# 绝不包含有状态组件（kafka/valkey/milvus/etcd/minio/数据库）——它们的重启/扩容属于高风险。
ALLOWED_TARGETS: set[str] = set(
    t.strip()
    for t in config.REMEDIATION_ALLOWED_TARGETS.split(",")
    if t.strip()
)

# 目标名的通用片段（字母数字 + - _），拼进每条操作正则里做目标校验。
_TARGET = r"[a-zA-Z0-9][a-zA-Z0-9._-]*"
# 命名空间片段：k8s 命名空间的合法字符（小写字母数字 + -），锚在正则里防注入。
_NS = r"[a-z0-9][a-z0-9-]*"


class LowRiskAction:
    """一条可枚举的低风险线上操作。

    - action：稳定的操作 ID（写进结构化输出 / 执行台账，供路由与报告使用）。
    - pattern：**整条命令**必须完整匹配的锚定正则（^…$）。命中即视为「白名单内的低风险操作」。
    - description：给 Agent 看的人类可读说明（拼进提示词的「可用处置手段」）。
    """

    def __init__(self, action: str, pattern: str, description: str) -> None:
        self.action = action
        self.description = description
        self._re = re.compile(pattern)

    def match(self, command: str) -> Optional[str]:
        """命令若命中本操作，返回被操作的目标实例名；否则返回 None。"""
        m = self._re.match(command.strip())
        if not m:
            return None
        return m.group("target")


# === 低风险操作白名单（可自动执行）===
# 三类等价的低风险止血手段，各有 k8s / docker 两套实现。稳定的 action ID 相同
# （restart_instance / migrate_instance / scale_resources），后端不同只是命令形态不同——
# 台账、路由、报告都只认 action ID，对后端无感知。
#
# k8s 后端（生产主线）：kubectl 操作 Deployment / 无状态工作负载，可选 `-n <ns>` 命名空间。
_NS_OPT = rf"(?:-n\s+{_NS}\s+|--namespace\s+{_NS}\s+)?"
_K8S_ACTIONS: list[LowRiskAction] = [
    # 滚动重启一个工作负载：`kubectl rollout restart deploy/<t>`。最常见的止血手段（逐个换 Pod，不断服务）。
    LowRiskAction(
        "restart_instance",
        rf"^kubectl\s+{_NS_OPT}rollout\s+restart\s+(?:deployment|deploy)/(?P<target>{_TARGET})$",
        "滚动重启工作负载：kubectl rollout restart deploy/<svc>（逐个换 Pod 止血，如清掉泄漏进程的内存、重置卡死状态）。",
    ),
    # 迁移/重建工作负载的 Pod：`kubectl delete pod -l app=<t>`（控制器立刻重建，等价于重新调度到新节点）。
    # 用 label selector 而非 Pod 名——Pod 名含随机 hash 且工作负载名可能带 `-`，按名解析目标有歧义；
    # 按 `app=<target>` 标签选择则目标唯一、可干净地做授权校验。
    LowRiskAction(
        "migrate_instance",
        rf"^kubectl\s+{_NS_OPT}delete\s+pod\s+(?:-l|--selector)\s+app=(?P<target>{_TARGET})$",
        "迁移/重建工作负载 Pod：kubectl delete pod -l app=<svc>（控制器立即重建，等价于重新调度到新节点）。",
    ),
    # 临时抬高工作负载资源上限：`kubectl set resources deploy/<t> --limits=cpu=..,memory=..`。缓解资源型故障。
    LowRiskAction(
        "scale_resources",
        rf"^kubectl\s+{_NS_OPT}set\s+resources\s+(?:deployment|deploy)/(?P<target>{_TARGET})\s+--limits=(?:cpu=[0-9.]+m?|memory=\d+[EPTGMK]i?)(?:,(?:cpu=[0-9.]+m?|memory=\d+[EPTGMK]i?))*$",
        "抬高工作负载资源上限：kubectl set resources deploy/<svc> --limits=cpu=..,memory=..（缓解 CPU/内存打满）。",
    ),
]

# docker 后端（本地默认，零门槛可复现）：单容器操作，语义与上面一一对应。
_DOCKER_ACTIONS: list[LowRiskAction] = [
    # 重启单实例：`docker restart <target>`（可选 -t 优雅停机秒数）。
    LowRiskAction(
        "restart_instance",
        rf"^docker\s+restart\s+(?:-t\s+\d+\s+)?(?P<target>{_TARGET})$",
        "重启单实例：docker restart <实例>（临时止血，如清掉泄漏进程的内存、重置卡死状态）。",
    ),
    # 迁移/重建单实例：`docker compose up -d --force-recreate <target>`
    # 或独立二进制 `docker-compose up -d --force-recreate <target>`
    # （有些环境只装了其中一种，两种都放行；先用 `docker compose version`/`docker-compose version` 确认本机哪个可用）。
    LowRiskAction(
        "migrate_instance",
        rf"^docker(?:\s+compose|-compose)\s+up\s+-d\s+--force-recreate\s+(?P<target>{_TARGET})$",
        "迁移/重建单实例：docker compose up -d --force-recreate <实例>（或 docker-compose，视本机装的是插件还是独立二进制）（把实例重新拉起，等价于重新调度）。",
    ),
    # 临时扩容单实例资源上限：`docker update --cpus <n> --memory <m> <target>`。
    LowRiskAction(
        "scale_resources",
        rf"^docker\s+update\s+(?:--cpus\s+[0-9.]+\s+|--memory\s+\d+[bkmgBKMG]*\s+|--memory-swap\s+\d+[bkmgBKMG]*\s+)+(?P<target>{_TARGET})$",
        "扩容单实例资源上限：docker update --cpus <n> --memory <m> <实例>（缓解 CPU/内存打满，副本级弹性）。",
    ),
]

# 按后端选定当前生效的白名单。默认 docker（本地零门槛），AIOPS_BACKEND=k8s 切到生产主线。
LOW_RISK_ACTIONS: list[LowRiskAction] = (
    _K8S_ACTIONS if config.REMEDIATION_BACKEND == "k8s" else _DOCKER_ACTIONS
)


def classify_command(command: str) -> dict[str, Any]:
    """判定一条 Bash 命令属于哪一类。这是 hook 做「放行/拦截」决策的唯一依据。

    返回 {"decision": "allow"|"deny"|"passthrough", ...}：
    - allow：命中低风险白名单，且目标在 ALLOWED_TARGETS 内 → Agent 可自动执行。
    - deny：命中低风险白名单，但目标不在白名单内（如想重启 kafka/db）→ 拦截。
    - passthrough：不是我们管辖的处置命令（比如只读查询）→ 交给下一层规则判断。
    """
    if not config.REMEDIATION_ENABLED:
        return {"decision": "passthrough"}

    cmd = command.strip()
    for act in LOW_RISK_ACTIONS:
        target = act.match(cmd)
        if target is None:
            continue
        if target not in ALLOWED_TARGETS:
            return {
                "decision": "deny",
                "action": act.action,
                "target": target,
                "reason": (
                    f"低风险操作 {act.action} 命中，但目标实例 '{target}' 不在可自动处置白名单内"
                    f"（允许：{sorted(ALLOWED_TARGETS)}）。有状态组件/未授权目标一律交人工。"
                ),
            }
        return {"decision": "allow", "action": act.action, "target": target}
    return {"decision": "passthrough"}


# === 执行台账（code-hard 记录 Agent 到底自动执行了哪些线上操作）===
# 这是「产出记录」的可信来源：不信任模型的自我陈述，而是由 hook 在**真正放行的那一刻**记录。
# 编排层据此决定路由（是否真的自动止血过），并落进最终报告。进程内、每次诊断前 reset。
_LEDGER: list[dict[str, Any]] = []


def reset_ledger() -> None:
    """每趟诊断开始前清空台账，保证记录只属于本次运行。"""
    _LEDGER.clear()


def record_execution(action: str, target: str, command: str) -> None:
    """hook 在放行一条低风险操作时调用，登记「执行了什么」。"""
    _LEDGER.append(
        {
            "action": action,
            "target": target,
            "command": command,
            "ts": _now(),
        }
    )


def get_ledger() -> list[dict[str, Any]]:
    """返回本次运行已放行执行的低风险操作台账（副本）。"""
    return [dict(x) for x in _LEDGER]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def actions_catalog_md() -> str:
    """把低风险操作白名单渲染成 markdown，拼进诊断 Agent 的提示词（告知有哪些手段）。"""
    backend = "k8s（kubectl）" if config.REMEDIATION_BACKEND == "k8s" else "docker"
    lines = [f"- `{a.action}`：{a.description}" for a in LOW_RISK_ACTIONS]
    targets = ", ".join(sorted(ALLOWED_TARGETS)) or "（未配置任何可处置目标）"
    return (
        f"当前处置后端：**{backend}**（仅下列命令形态会被放行；换后端由 `AIOPS_BACKEND` 控制）。\n"
        + "\n".join(lines)
        + f"\n\n可自动处置的目标（白名单，仅限这些工作负载）：{targets}"
    )
