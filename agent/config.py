"""集中配置：路由阈值、成本上限、各服务地址、凭证。

所有参数都能用环境变量覆盖，这样 eval 或不同环境切换时无需改代码。
（对应 .env.example 里的各项；未设置时用这里的默认值。）
"""
from __future__ import annotations

import os
from pathlib import Path

# --- 路径 ---
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = REPO_ROOT / "workspace"  # 代码修复 Agent clone 代码仓的工作目录

# --- 路由阈值 ---
# 置信度低于此值，诊断降级为人工处理（发飞书卡片）
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))

# --- 自动线上处置（低风险操作白名单）---
# 总开关：关掉后诊断 Agent 退回「纯只读」，所有线上操作都走飞书卡片交人工（旧行为）。
REMEDIATION_ENABLED = os.getenv("AIOPS_REMEDIATION_ENABLED", "1") == "1"
# 处置后端：决定「低风险白名单里放行的是哪一套命令、可观测/发版怎么查」。
#   - "k8s"（生产形态，文档主线）：kubectl rollout restart / delete pod / set resources。
#   - "docker"（本地默认，零门槛可复现）：docker restart / compose up / update。
# 默认 docker，保证使用者不起 k8s 集群也能一键跑通；想体验生产形态就设 AIOPS_BACKEND=k8s
# 并起一个 kind 集群（见 scripts/k8s/）。白名单与提示词都会据此切换，代码其余部分无感知。
REMEDIATION_BACKEND = os.getenv("AIOPS_BACKEND", "docker").strip().lower()
# k8s 后端下处置操作作用的命名空间。
K8S_NAMESPACE = os.getenv("AIOPS_K8S_NAMESPACE", "otel-demo")
# 允许被 Agent 自动重启/扩容的目标实例（有限集合，逗号分隔）。只放无状态业务容器/工作负载；
# 绝不放有状态组件（kafka/valkey/milvus/db）。见 agent/core/remediation.py。
# docker 后端里是容器名，k8s 后端里是 Deployment / 无状态工作负载名。
REMEDIATION_ALLOWED_TARGETS = os.getenv(
    "AIOPS_REMEDIATION_TARGETS", "recommendation,ad,frontend,cart,checkout,currency,payment,shipping,quote,email"
)

# --- 幂等去重 ---
# 同一指纹的告警在这个冷却窗口（秒）内只处理一次
DEDUP_TTL_SECONDS = int(os.getenv("DEDUP_TTL_SECONDS", str(30 * 60)))

# --- 成本控制 ---
# 每趟 query 的最大轮数 / 超时（秒），防止 Agent 无限循环烧钱
DIAGNOSE_MAX_TURNS = int(os.getenv("DIAGNOSE_MAX_TURNS", "25"))
DIAGNOSE_TIMEOUT_S = int(os.getenv("DIAGNOSE_TIMEOUT_S", "300"))
FIX_MAX_TURNS = int(os.getenv("FIX_MAX_TURNS", "25"))
FIX_TIMEOUT_S = int(os.getenv("FIX_TIMEOUT_S", "600"))
SCHEMA_RETRY_MAX = int(os.getenv("SCHEMA_RETRY_MAX", "2"))  # 结构化输出校验失败的最大重试次数

# --- 模型 ---
MODEL = os.getenv("AIOPS_MODEL", "opus")

# --- 可观测性地址（故障诊断处置 Agent 用 curl 只读查询）---
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:16686")
FLAGD_OFREP_URL = os.getenv("FLAGD_OFREP_URL", "http://localhost:8016")

# --- 飞书 ---
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")

# --- GitHub（代码修复 Agent 提 PR 用）---
# 模型：真实开源协作流。上游（upstream）是维护者维护的公开共享仓，使用者 fork 到自己账号后，
# Agent 把修复推到「使用者自己的 fork」，再向「上游」发起跨仓 PR——无需被邀请为协作者，
# 所有使用者的 PR 都汇聚到同一个上游仓，维护者一处即可 review。
#
# - GITHUB_TOKEN        ：使用者自己的 Personal Access Token（scope: repo/public_repo）。
# - GITHUB_UPSTREAM_OWNER/REPO ：共享上游仓（PR 的 base）。默认指向示例仓。
# - GITHUB_FORK_OWNER   ：使用者自己的 GitHub 用户名（Agent 从这里 clone、往这里 push、PR 的 head）。
#                         若与 UPSTREAM_OWNER 相同 → 退化为「同仓 PR」（维护者本人调试用）。
GITHUB_API_BASE = os.getenv("GITHUB_API_BASE", "https://api.github.com")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_UPSTREAM_OWNER = os.getenv("GITHUB_UPSTREAM_OWNER", "HuaiNan54321")
GITHUB_REPO = os.getenv("GITHUB_REPO", "recommendation")
GITHUB_FORK_OWNER = os.getenv("GITHUB_FORK_OWNER", "") or GITHUB_UPSTREAM_OWNER
# 默认分支名。GitHub 新仓默认 main；本项目沿用 master 以保持与分支保护/PR base 一致。
GITHUB_DEFAULT_BRANCH = os.getenv("GITHUB_DEFAULT_BRANCH", "master")

# --- 记忆 / RAG（Milvus 向量库 + BGE 中文 embedding）---
# 每条诊断存成一条「工单」向量；故障诊断处置 Agent 通过 in-process MCP 工具对历史工单做 agentic RAG。
# 所有失败都降级为空操作，绝不把异常抛进诊断主流程（项目理念：流程永不崩溃）。
RAG_ENABLED = os.getenv("AIOPS_RAG_ENABLED", "1") == "1"
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "aiops_tickets")
# BAAI/bge-small-zh-v1.5 -> 512 维；bge-base-zh -> 768 维。改模型记得同步 EMBEDDING_DIM。
EMBEDDING_MODEL = os.getenv("AIOPS_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "512"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
