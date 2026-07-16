# AIOps 故障诊断和修复 Agent

把告警变成**根因诊断 + 分级处置 + 自动修复**：低风险线上操作（滚动重启/迁移/抬高资源上限单个工作负载）Agent 自动执行止血、高风险操作（redis/db/删除/改集群/缩容）发飞书卡片交人工，代码 bug 类自动改码并提 PR（人工评审合并），混合根因两条腿都走。「能自动执行哪些操作」由代码层一份可枚举的白名单硬控（不靠提示词）。基于 **Claude Agent SDK** 单引擎、两趟、按风险分级处置。设计细节见 [`AIOps-Agent-技术方案.md`](AIOps-Agent-技术方案.md)。

**运行环境：以 Kubernetes 为主**——生产运维基本盘就是 k8s，诊断 Agent 的观测（`kubectl get/describe/top`）与自动止血（`kubectl rollout restart` / `delete pod` / `set resources`）都以 kubectl 为主形态。为了让使用者**零门槛先跑通**，默认后端是 **docker**（一台机器 `docker compose up` 即可，无需集群）；想体验生产形态就 `export AIOPS_BACKEND=k8s` 并起一个 kind 集群（见 [快速开始](#快速开始)）。两套后端语义一一对应，切换只改一个环境变量，代码其余部分无感知。

已完成从告警到 PR 的**完整端到端真实闭环验证**（内存泄漏 capstone）：真工作负载跑到 OOM 重启、Agent 实地观测定位到发版 commit、改成有界结构、测试从红转绿、提出一个推不上 master 的 PR。

```
告警 → 【故障诊断处置 Agent(只读诊断+低风险自动处置)】定位根因 + 给方案
        ├─ online_op 低风险(滚动重启/迁移Pod/抬资源上限,命中白名单) → Agent 自动执行止血 → 飞书卡片知会人工
        ├─ online_op 高风险(回滚版本/redis/db/删除/改集群/缩容)   → 飞书卡片(HITL) → 人工执行
        │    └─ + also_code_fix (混合根因)   → 上面止血 **并** 触发 代码修复 Agent 提 PR 根治(双走)
        ├─ code_fix  (代码 bug)              → 【代码修复 Agent(写)】clone→改→build+test→提PR
        ├─ info_only                         → 直接报告
        └─ confidence < 阈值                  → 飞书卡片，降级人工
诊断完成后 ─────────────────────────────────→ 编排层把工单向量化存入 Milvus (经验沉淀)
```

## 快速开始（Quick Start）

鉴权走本机**已登录的 `claude` CLI**（需已装 Node + claude CLI 并登录），无需配 `ANTHROPIC_API_KEY`。分两档，按想要的"含金量"选。

```bash
# 安装（Python 3.11+）
cd AIops-agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # FEISHU/GITHUB 等按需填；不填也能本地跑通逻辑
```

**档位 A：快速逻辑演示（1 分钟，无需 Docker）** —— 不依赖真实监控栈，Agent 据告警文本 + 排查手册 Skill 给出诊断，飞书卡片直接打印到终端：

```bash
source .env
python -m agent.run --alert alerts/s2.json --json-out reports/s2.json
# 终端打出诊断过程 + 路由决策；reports/s2.json 为结构化结果
```

**档位 B：全真栈端到端闭环（招牌 capstone）** —— 真跑内存泄漏服务到 OOM，Agent 实地观测→定位泄漏 commit→改码→build+test→提 PR（需 Colima/Docker，提 PR 还需配 `GITHUB_TOKEN`，详见 [下方完整快速开始](#快速开始)）：

```bash
./scripts/fetch-otel-demo.sh        # 拉 OTel Demo compose 到 vendor/
docker compose up -d                # OTel Demo + Prometheus + Jaeger + Milvus
./scripts/inject.sh s4              # 注入内存泄漏（等 1-2 分钟让内存爬升）
python -m agent.run --alert alerts/s4.json
```

> 完整的后端切换（docker / k8s）、GitHub fork 配置、Milvus 记忆等见 [下方「快速开始」详解](#快速开始)。

## 架构

代码按职责分三层，先读 `agent/run.py`（总控），再顺着分层往下看：

```
agent/
├── run.py              编排入口：把告警一路串到最终报告（最适合先读）
├── config.py           集中配置（全部可用环境变量覆盖）
├── agents/             ← 两个 Agent 主体（业务核心）
│   ├── diagnose.py     故障诊断处置 Agent：只读排查 + 定位根因 + 低风险自动处置
│   ├── code_fix.py     代码修复 Agent：改码 + build+test + 提 PR
│   └── prompts.py      两个 Agent 的提示词
├── core/               ← 运行底座（两个 Agent 共用）
│   ├── sdk_runner.py   对 Claude Agent SDK 的薄封装 + 成本/超时控制
│   ├── schema.py       结构化输出数据模型（Diagnosis / FixResult / ExecutedAction）
│   ├── remediation.py  低风险线上操作白名单（可枚举）+ 命令分级判定 + 执行台账
│   └── hooks.py        安全 hook：诊断按白名单放行低风险/拦其余、修复禁动线上
└── integrations/       ← 外部系统对接
    ├── ingest.py       告警接入 + 指纹幂等去重
    ├── feishu.py       飞书交互卡片
    ├── memory.py       诊断工单向量化存 Milvus + BGE 语义检索
    └── rag_tool.py     Agentic RAG 工具 search_past_incidents（只给 故障诊断处置 Agent）
```

| 层 | 模块 | 职责 |
|---|---|---|
| 编排 | [`agent/run.py`](agent/run.py) | 接入告警 → 幂等去重 → 故障诊断处置 Agent → 低置信度逃生口 → 路由 → report（CLI 入口） |
| 配置 | [`agent/config.py`](agent/config.py) | 集中配置：阈值/成本上限/各服务地址/凭证，全可环境变量覆盖 |
| 故障诊断处置 Agent | [`agent/agents/diagnose.py`](agent/agents/diagnose.py) | 只读调查 + schema 校验 + 重试 + 兜底降级 |
| 代码修复 Agent | [`agent/agents/code_fix.py`](agent/agents/code_fix.py) | clone 仓 → 改码 → build+test → 提 PR |
| 提示词 | [`agent/agents/prompts.py`](agent/agents/prompts.py) | 两个 Agent 的系统提示与用户提示 |
| SDK 封装 | [`agent/core/sdk_runner.py`](agent/core/sdk_runner.py) | `query()` + max_turns/超时/预算/usage |
| 结构化输出 | [`agent/core/schema.py`](agent/core/schema.py) | pydantic `Diagnosis` / `FixResult` + JSON schema |
| 安全 hook | [`agent/core/hooks.py`](agent/core/hooks.py) | PreToolUse 按白名单放行低风险 / 拦其余写变更 / 修复禁线上变更 |
| **处置白名单** | [`agent/core/remediation.py`](agent/core/remediation.py) | 可枚举的低风险线上操作（k8s/docker 两套，锚定正则 + 目标授权）+ 命令分级 + 执行台账 |
| ingest | [`agent/integrations/ingest.py`](agent/integrations/ingest.py) | 读告警 + 指纹幂等去重（内存 TTL） |
| 飞书 | [`agent/integrations/feishu.py`](agent/integrations/feishu.py) | 交互卡片（未配 webhook 时打印到 stdout） |
| **记忆/RAG** | [`agent/integrations/memory.py`](agent/integrations/memory.py) | 诊断工单向量化存 Milvus + BGE 语义检索历史工单（失败降级 no-op） |
| **Agentic RAG 工具** | [`agent/integrations/rag_tool.py`](agent/integrations/rag_tool.py) | in-process MCP 工具 `search_past_incidents`，故障诊断处置 Agent 自主检索历史相似工单 |
| Eval | [`eval/run.py`](eval/run.py) | 比对 `expected.json`，记录路由/成本/降级 |

## 安全边界

- **诊断只读 + 低风险白名单**：故障诊断处置 Agent `allowed_tools=[Bash,Read,Grep,Glob]` + PreToolUse hook。写/变更命令默认全拦（`kubectl apply/scale/exec`、`docker rm`、`redis-cli`、`git push`、`rm -rf`…）；只有命中 [`remediation.py`](agent/core/remediation.py) 里**可枚举的低风险白名单**才放行自动执行。白名单按后端各一套、语义对应：k8s 为 `kubectl rollout restart deploy/<t>` / `kubectl delete pod -l app=<t>` / `kubectl set resources deploy/<t> --limits=...`，docker 为 `docker restart` / `compose up --force-recreate` / `docker update`；均为锚定正则 `^…$` + 目标授权。锚定杜绝 `&&`/`;`、以及藏在 `-n <ns>` flag 里的命令注入绕过。
- **风险分级处置**：低风险操作 Agent 自动执行（记入执行台账，事后发卡知会人工复核）；高风险操作（回滚版本/redis/db/删除/改集群/缩容/动有状态组件）绝不自动执行，只发卡交人工。「执行了什么」以台账为准（`AIOPS_REMEDIATION_ENABLED=0` 可一键退回纯只读）。
- **代码修复**：代码修复 Agent 仅推特性分支 + 提 PR；master **分支保护** + PR 人工评审；hook 禁止线上变更/强推/推 master。
- **build+test 通过才提 PR**：未通过则降级飞书卡片。
- **低置信度转人工**：`confidence < CONFIDENCE_THRESHOLD`（默认 0.6）。
- **成本上限**：每次 `query()` 设 `max_turns` / `asyncio.timeout` / `max_budget_usd`。
- **隔离**：故障诊断处置 Agent 为加载 Skill 开 `setting_sources=["project"]`（仅项目作用域）；`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`（用自建 Milvus 记忆，不用 SDK auto-memory）、独立 cwd。

## 记忆 + Agentic RAG + Skill

让 Agent 从"每次从零诊断"升级为"带经验积累、能跨服务迁移相似故障模式"的运维 Agent。

- **Agent Skills**：4 个排查手册（内存/依赖/CPU/队列）以 `.claude/skills/<name>/SKILL.md` 形式存在，故障诊断处置 Agent 按告警类型**按需自动加载**（比一次性全塞进系统提示省 token）。开关：`skills="all"` + `setting_sources=["project"]`。
- **记忆（Milvus 向量库）**：每次诊断完，编排层把工单（告警+根因+证据+处置）用 **BGE 中文 embedding** 向量化，存进 **Milvus**（`agent/integrations/memory.py`）。写入在编排层做，不破坏 故障诊断处置 Agent 只读边界。
- **Agentic RAG**：历史工单检索做成 故障诊断处置 Agent 的 **in-process MCP 工具** `search_past_incidents`（`agent/integrations/rag_tool.py`）——Agent 自主决定何时检索、查什么（真 agentic，而非编排层强制注入）。工具只读 Milvus。
- **优雅降级**：`AIOPS_RAG_ENABLED=0` 或 Milvus/模型不可用时，记忆子系统整体降级为 no-op，**不影响核心诊断闭环**（与"流程永不崩溃"一致）。

技术栈：Milvus standalone（etcd+minio+milvus 三容器）+ BGE（`BAAI/bge-small-zh-v1.5`，本地离线，ModelScope 下载）+ 自定义 MCP 工具 + Agent Skills。

```bash
# 起 Milvus（仅向量库,不含 OTel 全栈）
./scripts/start-milvus.sh
# 下载 BGE 模型（国内走 ModelScope,一次即可）
modelscope download BAAI/bge-small-zh-v1.5
```

## 快速开始

### 1. 安装
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # FEISHU/GITHUB 等按需填；不填也能本地跑通逻辑
```
> 鉴权走本机已登录的 `claude` CLI（需已安装 Node + claude CLI 并登录），无需单独配 `ANTHROPIC_API_KEY`。

### 2. 不依赖真实栈先跑通逻辑
未配飞书 webhook 时，卡片直接打印到 stdout；故障诊断处置 Agent 会尝试用 curl 连 Prometheus/Jaeger，连不上则据告警文本与排查手册 Skill 给出（较低置信度的）诊断。
```bash
source .env  # 导出环境变量
python -m agent.run --alert alerts/s2.json --json-out reports/s2.json
```

### 3. 全真栈端到端闭环

被修复的目标服务仓托管在**公开 GitHub**（真实开源协作流），使用者各自 fork 后由 Agent 提跨仓 PR，无需被邀请为协作者。

**（维护者，一次性）** 建/刷新公开上游仓：
```bash
./scripts/seed-github.sh            # 用 gh 建公开 recommendation 仓 + 基线/泄漏 commit + master 分支保护
                                    # 默认上游：github.com/HuaiNan54321/recommendation
```

**（使用者）** fork 上游仓 → 配自己的 token：
```bash
# 1) 在网页把 https://github.com/HuaiNan54321/recommendation Fork 到自己账号
# 2) https://github.com/settings/tokens 建 PAT（勾 public_repo）
# 3) 在 .env 里填：
#    GITHUB_TOKEN=<你的 PAT>
#    GITHUB_UPSTREAM_OWNER=HuaiNan54321   GITHUB_REPO=recommendation
#    GITHUB_FORK_OWNER=<你的 GitHub 用户名>
```

**跑闭环（默认 docker 后端，零门槛）：**
```bash
./scripts/fetch-otel-demo.sh        # 拉取 OTel Demo compose 到 vendor/
docker compose up -d                # OTel Demo + Prometheus + Jaeger + Milvus
source .env                         # 导出 GITHUB_* 等环境变量

# 内存泄漏端到端闭环（capstone）
./scripts/inject.sh s4                       # 从泄漏 commit 真 build 镜像 → docker run --memory=128m，内存开始单调上升
# 等 1-2 分钟让内存爬升：docker stats recommendation / curl localhost:8080/metrics
python -m agent.run --alert alerts/s4.json   # 故障诊断处置 Agent 实地观测泄漏→定位 commit→代码修复 Agent build+test→提 PR
# → 你的 fork 上出现修复分支，并向上游仓发起一条跨仓 PR（推不上上游 master、含 build+test 验证摘要）
```

**跑闭环（k8s 后端，生产形态，可选进阶）：**
```bash
# 1) 起一个本地 kind 集群（含 metrics-server，让 kubectl top 有数据）
./scripts/k8s/start-kind.sh
export AIOPS_BACKEND=k8s AIOPS_K8S_NAMESPACE=otel-demo
source .env

# 2) 注入 s4：build 泄漏镜像 → kind load → kubectl apply Deployment(memory limit 128Mi)
./scripts/inject.sh s4
# 等 1-2 分钟让内存爬升：kubectl top pod -l app=recommendation -n otel-demo
python -m agent.run --alert alerts/s4.json   # 诊断 Agent 用 kubectl describe/top 观测 OOMKilled+重启→定位 commit→提 PR
# 混合根因下会先 kubectl rollout restart 授权工作负载止血、再提根治 PR → route=auto_remediated_and_code_fix_pr
```

内存泄漏服务是自带的轻量 Python 服务（`scripts/fixtures/recommendation/`，纯 stdlib，自驱负载使内存真实爬升），不依赖 OTel demo 镜像。`seed-github.sh` 保证 **GitHub 仓码 = 运行的工作负载码 = 代码修复 Agent 的修复目标** 三方一致，构成逻辑自洽的真实闭环。

### 端到端闭环实测（Colima 全真栈，docker 后端）

故障诊断处置 Agent 用 `docker stats`/`docker inspect`（k8s 后端则是 `kubectl top`/`kubectl describe`）实地观测到真实运行的 recommendation 工作负载内存从 ~15MiB 单调爬升趋向 128MiB 上限 → 触碰上限被 OOMKilled → 重启（RestartCount=1）；据镜像 tag + `deploys.log` + `git show` 定位泄漏 commit（confidence 0.9，诊断全程只读）；代码修复 Agent clone 仓改有界 deque、`pytest`（含 `test_memory_is_bounded`）由失败转 2 passed → 向上游仓提出 GitHub PR（推不上 master，verified=true）。诊断与修复两半都基于真实运行的服务。

## Eval
```bash
python -m eval.run --dry-run        # 只校验 fixture（不调用 Agent）
python -m eval.run                  # 全部
python -m eval.run --only s2 s4     # 跑指定子集，输出 通过/成本/降级
```

## 配置（环境变量，见 `.env.example`）
`AIOPS_BACKEND`（`docker` 默认 / `k8s`）、`AIOPS_K8S_NAMESPACE`（默认 `otel-demo`）、`AIOPS_REMEDIATION_ENABLED`、`AIOPS_REMEDIATION_TARGETS`、`FEISHU_WEBHOOK_URL`、`GITHUB_TOKEN/UPSTREAM_OWNER/REPO/FORK_OWNER`、`PROMETHEUS_URL`、`JAEGER_URL`、`CONFIDENCE_THRESHOLD`、`DIAGNOSE_MAX_TURNS/TIMEOUT_S`、`FIX_MAX_TURNS/TIMEOUT_S`、`AIOPS_MODEL`、`AIOPS_RAG_ENABLED`、`MILVUS_URI`、`AIOPS_EMBEDDING_MODEL`。

## 本机环境备注（Colima + 国内镜像）
- **后端选择**：默认 docker（本地零门槛）；生产形态用 `AIOPS_BACKEND=k8s` + 本地 kind 集群（`scripts/k8s/start-kind.sh`）。文档以 k8s 为主线，docker 是等价的本地默认实现。
- Docker 运行时用 **Colima**：`colima start --cpu 6 --memory 12 --disk 60`；compose 用 standalone `docker-compose`。kind 也跑在 Colima 的 docker 之上。
- 国内直连 docker.io/ghcr.io/quay.io 超时，已配镜像：Colima daemon.json 加 `registry-mirrors`（daocloud/1panel，仅 docker.io）；ghcr 镜像走 `ghcr.nju.edu.cn`，quay 用 docker.io 等价镜（`vendor/otel-demo/.env` 已改，备份 `.env.bak`）。
- Colima 下 collector 的 `docker_stats` receiver 会崩（Docker API 太旧），已从 `otelcol-config.yml` metrics pipeline 移除；内存观测走 `docker stats`/`docker inspect` + 服务自身 `/metrics`。生产换 cAdvisor/kube-state-metrics 即回 PromQL 路径。
- BGE 模型走 ModelScope（HuggingFace 国内不可达），缓存在 `~/.cache/modelscope`，离线可加载。
