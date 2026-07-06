# AIOps 故障诊断和修复 Agent — 技术方案

> **一句话**：告警进来 → **故障诊断处置 Agent** 定位根因并给处置类型 → 按**风险分级**处置：低风险线上操作（滚动重启/迁移 Pod/抬高资源上限单个工作负载）Agent **自动执行止血**、高风险操作发**飞书卡片**交人工、代码 bug 交 **代码修复 Agent 改码提 PR**、混合根因**两条都走**。诊断带 **Agent Skills**（排查手册按需加载）+ **Agentic RAG**（Milvus 历史工单语义检索），每次诊断结果沉淀进向量库形成经验积累。
>
> **引擎**：**Claude Agent SDK** 单引擎、串行两趟（诊断 / 修复），编排是普通 async Python。鉴权走本机已登录的 `claude` CLI（无需 `ANTHROPIC_API_KEY`）。
>
> **运行环境（k8s 为主，docker 为本地默认）**：生产运维基本盘是 Kubernetes，所以观测与自动止血都以 kubectl 为主形态（`kubectl get/describe/top`、`kubectl rollout restart`/`delete pod`/`set resources`）。为让使用者零门槛先跑通，默认后端是 docker（`docker compose`，无需集群），`AIOPS_BACKEND=k8s` 一键切到生产形态（起本地 kind 集群）。两套后端语义一一对应，白名单与提示词据此切换，代码其余部分对后端无感知。
>
> **安全边界（风险分级，工具层硬控）**：诊断 Agent 只读 + 一份**可枚举的低风险操作白名单**（`remediation.py`，k8s/docker 两套锚定正则 + 目标授权，PreToolUse hook 硬控放行/拦截）——低风险自动执行、高风险绝不自动执行只发卡；「能执行哪些操作」由代码枚举而非提示词，执行记录以「执行台账」为准而非模型自述；代码修复只推特性分支 + 提 PR（master 分支保护 + 人工评审），且 build+test 通过才提 PR；低置信度降级人工。

---

## 1. 目标与处置分流

```
告警 → 【故障诊断处置 Agent(只读诊断+低风险自动处置)】定位根因 + 给处置类型(remediation_type)
        ├─ online_op 低风险(滚动重启/迁移Pod/抬资源上限,命中白名单) → Agent 自动执行止血 → 卡片知会人工 (route=auto_remediated)
        ├─ online_op 高风险(回滚版本/redis/db/删除/改集群/缩容)   → 飞书卡片(HITL) → 人工执行 (route=feishu_online_op)
        │    └─ + also_code_fix (混合根因)         → 上面止血 并 触发 代码修复 Agent 提 PR 根治(双走)
        ├─ code_fix  (代码 bug)                    → 【代码修复 Agent(写)】clone→改→build+test→提 PR
        ├─ info_only (只需告知)                    → 直接报告
        └─ confidence < 阈值(0.6)                  → 飞书卡片，降级人工
```

差异化：打通 **infra 根因 → 处置分流 →（代码类）代码行级根因 + 自动 PR** 的闭环，且线上操作一律人工把关、混合根因两条腿走路（先止血再根治）。

---

## 2. 核心设计原则

1. **复用现成 CLI**：给 Agent **bash + 现成 CLI**（kubectl/docker/git/curl/rg…）+ 一份"可用工具与端点"说明，Agent 像 Claude Code 一样自己查、连真实服务。
2. **真实 demo 栈**：起真服务 + 真可观测 + 真 Git + 真向量库，bash/CLI 有真东西可连。生产形态跑在 kind（k8s）上、本地默认跑在 docker-compose 上，同一份泄漏 fixture 两处都能真机 OOM。
3. **k8s 为主、docker 兜底的双后端**：观测与自动止血以 kubectl 为主形态（生产基本盘），`AIOPS_BACKEND` 切换；docker 后端是零门槛的等价实现，语义一一对应。
4. **单引擎、两趟、按风险分级处置**：同一个 SDK `query()` 跑两次（故障诊断处置 Agent 只读诊断+低风险自动处置、代码修复 Agent 可写修复），编排用普通 async Python。
6. **安全分级**（见 §11）：诊断 Agent 的线上操作由**可枚举的低风险白名单**在工具层硬控（低风险自动执行、高风险交人工），能执行什么由代码枚举而非提示词；代码修复靠 master 分支保护 + PR 评审 + build/test 门禁兜底。
7. **流程永不崩溃**：结构化输出校验失败有重试+兜底降级；成本触顶/超时返回降级结果；记忆/RAG 子系统失败静默降级为 no-op——任何附加能力挂掉都不拖垮核心诊断闭环。

---

## 3. 架构与代码地图

```
 alert(alerts/*.json) ─▶ ingest(幂等去重) ─▶ 【故障诊断处置 Agent(只读)】─▶ remediation_type?
                                              工具: Bash/Read/Grep/Glob          │
                                              + Skill(排查手册) + RAG(历史工单)    │
        ┌───────────────────┬─────────────────────┬──────────────────────────────┴────┐
    online_op            code_fix              info_only                      confidence<0.6
    飞书卡片(HITL)        【代码修复 Agent(写)】      直接报告                        飞书卡片(降级人工)
    (+also_code_fix       clone→改→build+test
     → 并行提 PR)          →push 特性分支→提 PR
                    ┌──────────────────────────────────────────┐
 诊断完成后 ────────▶│ 编排层把工单向量化存入 Milvus (经验沉淀)      │
                    └──────────────────────────────────────────┘
```

| 模块 | 文件 | 职责 |
|---|---|---|
| 编排 | `agent/run.py` | ingest→去重→故障诊断处置 Agent→低置信逃生口→路由（含混合根因双走）→report→存工单。CLI 入口 |
| 故障诊断处置 Agent | `agent/agents/diagnose.py` | 只读调查 + schema 校验 + 重试 + 兜底降级；挂 Skill + RAG 工具 |
| 代码修复 Agent | `agent/agents/code_fix.py` | clone 仓 → 改码 → build+test → 提 PR |
| SDK 封装 | `agent/core/sdk_runner.py` | `query()` + max_turns/超时/预算/usage；条件挂 mcp_servers/skills |
| 安全 hook | `agent/core/hooks.py` | PreToolUse：诊断禁写、修复禁线上变更/禁推 master |
| 结构化输出 | `agent/core/schema.py` | pydantic `Diagnosis`/`FixResult` + 手写 JSON schema |
| 提示词 | `agent/agents/prompts.py` | 两个 Agent 的系统提示 append + 用户提示 |
| 记忆/RAG | `agent/integrations/memory.py` | 工单向量化存 Milvus + BGE 语义检索（失败降级 no-op） |
| RAG 工具 | `agent/integrations/rag_tool.py` | in-process MCP 工具 `search_past_incidents`（只给 故障诊断处置 Agent） |
| 飞书 | `agent/integrations/feishu.py` | 交互卡片；未配 webhook 时打印 stdout |
| ingest | `agent/integrations/ingest.py` | 读告警 + 指纹幂等去重（内存 TTL） |
| Eval | `eval/run.py` | 比对 `expected.json`，记录路由/成本/降级 |

- 两个执行体都用 **Claude Agent SDK `query()`**，区别在 **权限/凭证/工作目录/系统提示/挂载工具**。
- 编排是普通 async Python（ingest + 路由 + report + 存工单为胶水层）。

---

## 4. 技术栈

- **Python 3.11**，async；venv 在 `.venv`。
- **`claude-agent-sdk`**（v0.2.105）：唯一 Agent 引擎（`query()` + 内置 `Bash/Read/Write/Edit/Glob/Grep`）。鉴权走本机 `claude` CLI。
- demo 栈：**k8s 为主**（本地 kind 集群，`scripts/k8s/`）/ docker-compose 兜底（`include` 上游 OTel Demo `vendor/otel-demo/`）+ 自加 **Milvus** standalone（etcd+minio+milvus 三容器）。被修复的服务代码仓托管在**公开 GitHub**（不在集群/compose 内）。运行时用 **Colima**（非 Docker Desktop），docker 后端用 standalone `docker-compose`，k8s 后端用 kind（跑在 Colima docker 之上）+ `kubectl`。
- **飞书**：自定义机器人 webhook 卡片；未配 `FEISHU_WEBHOOK_URL` 时打印 stdout（本期只需"具备能力"）。
- **记忆 / RAG**：**Milvus** 向量库 + **BGE 中文 embedding**（`BAAI/bge-small-zh-v1.5`，本地离线、ModelScope 下载）；故障诊断处置 Agent 通过 in-process MCP 工具做 agentic RAG（见 §10）。
- 依赖（`requirements.txt`）：`claude-agent-sdk`、`pydantic>=2`、`pymilvus>=2.4`、`sentence-transformers>=2.2`、`modelscope>=1.9`。
- 模型：`config.MODEL` 默认 `"opus"`（`AIOPS_MODEL` 可覆盖）。

---

## 5. Agent 的"工具面"与排查手册

给 Agent 的是 **bash + 现成 CLI** + 系统提示里的一份"可用工具/端点"说明。

工具面按后端渲染（`prompts.py` 据 `AIOPS_BACKEND` 切换），**k8s 为主形态**：

| 信号 | k8s（主）命令 | docker（本地默认）命令 |
|---|---|---|
| 工作负载状态/重启/OOM | `kubectl get pod` / `kubectl describe pod <pod>`（`OOMKilled`/`Last State`/`RestartCount`） / `kubectl top pod` | `docker ps` / `docker stats --no-stream` / `docker inspect --format '{{.RestartCount}} {{.State.OOMKilled}}'` |
| 日志 | `kubectl logs -l app=<svc> --tail N` | `docker logs <svc> --tail N` |
| 指标 | `curl :9090/api/v1/query`（PromQL） | 同左 |
| 调用链 trace | `curl :16686/api/traces?service=<svc>...`、`/api/services`、`/api/dependencies`（Jaeger） | 同左 |
| 发版历史 | `kubectl rollout history deploy/<svc>` / 镜像 tag（含 git SHA）/ `deploys.log` | 镜像 tag / `deploys.log` |
| 码仓 | `git log/show/diff`（只读定位可疑 commit） | 同左 |
| 排查手册 | **Agent Skill 按需自动加载**（`.claude/skills/`，见 §10.1） | 同左 |
| 历史工单 | **`search_past_incidents` MCP 工具**（见 §10.3） | 同左 |

> **三类信号交叉关联**：Prometheus（量）+ Jaeger（链路）+ logs（细节）。依赖型根因用 Jaeger 佐证报错的那一跳 span。
>
> **观测通道现实**：本地 kind / Colima 下 Prometheus 可能无 cAdvisor/kube-state-metrics 容器内存指标；因此 s4 内存观测实际走 `kubectl top`/`kubectl describe`（docker 后端为 `docker stats`/`docker inspect`）+ 服务自身 `/metrics`（排查手册已列为起手式）。生产集群装齐 cAdvisor/kube-state-metrics 即回纯 PromQL 路径。

---

## 6. demo 栈（k8s 为主 / docker 兜底）

- **OpenTelemetry Demo**：~10 微服务 + Prometheus + Jaeger + flagd 故障注入（`include` 自 `vendor/otel-demo/`）。
- **GitHub（公开上游仓）**：托管服务代码仓、承载 PR，master 开启分支保护（`enforce_admins`，PR-only）。真实开源协作流——使用者 fork 上游到自己账号，Agent 用使用者自己的 token 推特性分支到其 fork，再向上游发起**跨仓 PR**，无需被邀请为协作者，所有 PR 汇聚到同一上游仓。
- **Milvus standalone**：etcd + minio + milvus 三容器，存历史诊断工单向量。`scripts/start-milvus.sh` 可单独拉起（不起 OTel 全栈）。
- `scripts/`：
  - `seed-github.sh`：用 `gh` 建公开 `recommendation` 仓 → 基线 commit（有界）+ 泄漏 commit（无界）+ master 分支保护 + 写 `deploys.log`（镜像 tag↔SHA↔时间）。
  - `k8s/start-kind.sh`：起本地 kind 集群 + metrics-server（`kubectl top` 有数据）+ `otel-demo` 命名空间。k8s 后端入口。
  - `k8s/recommendation-leak.yaml`：s4 泄漏版 Deployment（memory limit 128Mi，等价 docker `--memory=128m`）+ Service，label `app=recommendation` 供迁移止血命中白名单。
  - `inject.sh <s1|s2|s3|s4>`：s1–s3 翻 flagd flag；**s4 真实部署**——从泄漏 commit `docker build` 镜像后，按 `AIOPS_BACKEND`：k8s 走 `kind load` + `kubectl apply`（Pod OOMKilled+重建），docker 走 `docker run --memory=128m`。
  - `reset.sh`：flag 复位 + 删泄漏工作负载（k8s 删 Deployment/Service，docker 删容器）。
- **国内镜像坑**：docker.io 走 Colima daemon registry-mirror（daocloud/1panel）；ghcr → `ghcr.nju.edu.cn`；quay（etcd）→ `quay.dockerproxy.net`。BGE 模型走 ModelScope（HuggingFace 国内不可达）。

---

## 7. 编排（`agent/run.py`）

真实路由逻辑（`run` 是对外入口，内部 `_run_inner` 做诊断+路由，`run` 末尾统一存工单）：

```python
async def run(alert):
    report = await _run_inner(alert)   # 诊断 + 路由
    memory.store_ticket(report)        # 经验沉淀(best-effort, 失败/禁用则 no-op)
    return report

async def _run_inner(alert):
    if seen_recently(alert_fingerprint(alert)):        # 幂等去重
        return _report(alert, None, route="skipped_duplicate", ...)
    dr = await diagnose(alert)                          # 故障诊断处置 Agent
    diag = dr["diagnosis"]

    if diag.confidence < CONFIDENCE_THRESHOLD:          # 低置信逃生口 → 人工
        send_feishu_card(diag, ...);  return _report(..., route="feishu_low_confidence")

    if diag.remediation_type == "online_op":
        auto_done = bool(diag.executed_actions)         # 看执行台账（代码可信记录），非模型自述
        send_feishu_card(diag, routing_note=_auto_note(diag) if auto_done else None)
        if diag.also_code_fix:                          # 混合根因 → 并行提 PR
            fr = await code_fix(diag.model_dump(by_alias=True))
            if fr["fix"].verified:
                route = "auto_remediated_and_code_fix_pr" if auto_done else "online_op_and_code_fix_pr"
            else:
                route = "auto_remediated" if auto_done else "feishu_online_op"
            return _report(..., route=route, fix=fr["fix"])
        return _report(..., route="auto_remediated" if auto_done else "feishu_online_op")

    if diag.remediation_type == "code_fix":
        fr = await code_fix(diag.model_dump(by_alias=True))
        if not fr["fix"].verified:                      # build/test 未过 → 降级飞书
            send_feishu_card(diag, ...);  return _report(..., route="feishu_fix_unverified")
        return _report(..., route="code_fix_pr", fix=fr["fix"])

    return _report(..., route="info_only")              # info_only
```

路由值：`skipped_duplicate` / `feishu_low_confidence` / `feishu_online_op` / `auto_remediated` / `online_op_and_code_fix_pr` / `auto_remediated_and_code_fix_pr` / `code_fix_pr` / `feishu_fix_unverified` / `info_only`。其中 `auto_remediated`(*) 与 `feishu_online_op` 的区别只在于**执行台账里有没有记录到已放行执行的低风险操作**。

### 7.1 故障诊断处置 Agent 结构化产出（schema + 校验 + 重试 + 兜底）

整条路由押在 故障诊断处置 Agent 的 JSON 上，因此强制 schema 校验。`agent/core/schema.py` 的 `Diagnosis`：

| 字段 | 类型/取值 |
|---|---|
| `summary` | str |
| `kind` | `dependency` / `resource` / `deploy_regression` / `config` |
| `suspect_service` | str |
| `remediation_type` | `online_op` / `code_fix` / `info_only` |
| `remediation_detail` | str |
| `confidence` | float 0~1 |
| `evidence` | list[str]，非空，每条可追溯（PromQL/trace/日志行） |
| `time_window` | 可选 `{from, to}` |
| `also_code_fix` | 可选 bool（默认 false）——混合根因标记，见 §7.2 |
| `executed_actions` | list[ExecutedAction]——已自动执行的低风险处置。**不由模型填**，编排层从执行台账回填（故意不进 JSON schema） |

- 优先用 SDK structured output（`output_format={"type":"json_schema","schema":DIAGNOSIS_JSON_SCHEMA}`），读 `ResultMessage.structured_output`；回退解析 `result` 文本（容忍 ```json 代码块）。
- pydantic 校验失败 → 把错误回灌重试（默认最多 2 次）；仍失败 → 返回 `Diagnosis.fallback()`（`confidence=0`、`info_only`），由低置信逃生口降级人工，**流程不中断**。
- JSON schema 手写（`additionalProperties:False`），加字段须同步改 pydantic 与 schema 两处。

### 7.2 路由：低置信度逃生口 + 混合根因双走

- **`confidence < 0.6` → 飞书卡片转人工**（`CONFIDENCE_THRESHOLD` 可配）。代码修复是写操作、风险最高，仅高置信度进 代码修复 Agent。
- **低风险自动处置**：当 `online_op` 的止血手段命中低风险白名单（重启/迁移/扩容授权实例）时，诊断 Agent 直接用 Bash 执行止血（hook 放行 + 记台账），编排层看 `executed_actions` 非空 → route=`auto_remediated`（卡片变为知会人工复核）；未执行/高风险 → `feishu_online_op` 交人工。**「执行了没有」以台账为准，不信模型自述**。
- **混合根因（双走）**：现实问题常"既需线上止血、又需改代码根治"（典型：发版引入的内存泄漏——重启/回滚止血但不改代码下次复发）。故障诊断处置 Agent 报 `remediation_type=online_op` 且置 `also_code_fix=true`；编排层**两条都走**：低风险止血 Agent 已自动做掉（或发卡交人工），并行触发 代码修复 Agent 改码提 PR。route 取决于是否自动止血 × PR 是否 verified：`auto_remediated_and_code_fix_pr` / `online_op_and_code_fix_pr`（PR 成）或 `auto_remediated` / `feishu_online_op`（PR 未 verified，止血仍有效）。这比"二选一"更贴近真实 SRE 实践。
  - 设计上刻意用**可选布尔**而非扩 `remediation_type` 枚举，避免破坏已跑通的纯路径。

### 7.3 告警 ingest 与幂等

- 读静态 `alerts/*.json`（生产可接 Alertmanager webhook 做归一化，接口不变）。
- 幂等：`fingerprint = hash(告警名 + 服务 + 关键标签)`，同指纹在 TTL（默认 30min）内只处理一次；用内存字典 + TTL（多实例部署可换 Redis）。

---

## 8. 两个 Agent 的实现（Claude Agent SDK）

### 8.0 循环成本控制（`agent/core/sdk_runner.py`，两个 Agent 都设）

- `max_turns`：诊断默认 15、修复默认 25（可配）。
- 整体超时：`asyncio.timeout(...)` 包住 `query()`（诊断默认 300s、修复 600s）。
- token/成本：从 `ResultMessage.usage`/`total_cost_usd` 累计。
- **失败可观测**：**SDK 命中 max_turns 会抛异常**（不是 yield 错误消息），已用 try/except 转成 degraded 结果；超时/预算/异常都返回带 `degraded` 标记的结果，交路由逃生口处理。

### 故障诊断处置 Agent（只读诊断 + 低风险自动处置）

- `allowed_tools=["Bash","Read","Grep","Glob"]`（+ RAG 工具 `mcp__aiops_rag__search_past_incidents`，RAG 启用时）。
- `cwd=REPO_ROOT`（只读访问 `deploys.log`、`.claude/skills/`）。
- hook `guard_diagnose_ops`：PreToolUse 判定顺序——命中 `remediation.py` 低风险白名单且目标授权 → **allow + 记执行台账**；命中白名单但目标未授权 → deny；命中写/变更/高风险命令（`kubectl apply|delete|scale|exec|…`/`helm`/`docker rm|stop|exec`/`redis-cli`/`mysql|psql`/`git push`/`rm -rf`/`flushall|drop table`…）→ deny；变更类 docker 未被白名单接住 → 兜底 deny；其余（只读）放行。
- 低风险白名单（`remediation.py`，可枚举，按 `AIOPS_BACKEND` 选一套；action ID 相同、后端语义对应）：
  - **k8s（主）**：`restart_instance`（`kubectl rollout restart deploy/<t>`）、`migrate_instance`（`kubectl delete pod -l app=<t>`）、`scale_resources`（`kubectl set resources deploy/<t> --limits=cpu=..,memory=..`）。
  - **docker（本地默认）**：`restart_instance`（`docker restart <t>`）、`migrate_instance`（`docker compose up -d --force-recreate <t>`）、`scale_resources`（`docker update --cpus/--memory <t>`）。
  - 每条锚定正则 `^…$`（防 `&&`/`;` 及藏在 `-n <ns>` flag 里的注入）+ 目标须在 `ALLOWED_TARGETS`（无状态业务工作负载）内。`AIOPS_REMEDIATION_ENABLED=0` 退回纯只读。
- 系统提示：preset `claude_code` + append（工具清单 + 排查 playbook + 安全边界 + 低风险白名单告知 + remediation_type 判定 + 处置执行规则 + 结构化输出要求）。
- 挂 `skills="all"`（`setting_sources=["project"]` 发现 `.claude/skills/`）+ `mcp_servers={"aiops_rag": ...}`。

### 代码修复 Agent 代码修复（写；在 code_fix 或混合根因触发时）

- 输入：诊断上下文 + 已 clone 的仓（`clone_service_repo` clone 进 `workspace/<repo>`，token 嵌入 clone URL）。
- `allowed_tools=["Bash","Read","Write","Edit","Grep","Glob"]`；`cwd` 是 clone 仓。
- hook `deny_online_ops`：禁 `kubectl`/`helm`/`docker rm`、禁 `git push --force`、**禁推 master/main**（正则拦截）。
- 全局 `disallowed_tools=["Bash(rm -rf *)","Bash(git push --force *)"]`。
- 行为：定位可疑 commit/代码 → 改码 → **§8.2 build+test 验证** → 通过才建特性分支 + push 到自己的 fork + 用 GitHub API 向上游发起跨仓 PR。

### 8.2 修复验证：build + test 通过才提 PR

代码修复 Agent 系统提示强制把"是否提 PR"绑定在验证结果上：

1. 只改根因相关代码（如无界结构 → `collections.deque(maxlen=N)`）。
2. clone 仓内验证：`pip install -r requirements.txt`（若有）→ `python -m py_compile $(git ls-files '*.py')` → `pytest`（含 `test_memory_is_bounded` 回归用例）。
3. build/test 全过才建分支 push 提 PR；失败则在轮次内修正重跑，仍失败放弃、返回 `verified=false` + 失败摘要。
4. PR 描述含：根因、改动点、验证摘要（命令+结果）、关联可疑 commit/告警。
5. 编排兜底：`verified=false` 时转飞书卡片告知人工。

`FixResult`（`agent/core/schema.py`）：`verified` / `pr_url` / `build_cmd` / `test_cmd` / `verify_log` / `changed_files` / `degraded`。

---

## 9. 场景与内存泄漏 capstone

覆盖四类根因 + 低置信度降级，用一组告警 fixture（`alerts/`）驱动：

| 场景 | 触发 | 类型 | 走向 |
|---|---|---|---|
| 依赖故障 | flagd `productCatalogFailure` | online_op | 低风险重启授权工作负载可 `auto_remediated`，否则飞书卡片 |
| 资源型（CPU） | 直接打满 ad CPU | online_op | ad 在白名单，可 `kubectl rollout restart deploy/ad` / `set resources`（docker 后端 `docker restart/update ad`）自动止血 `auto_remediated` |
| 队列积压 | flagd `kafkaQueueProblems` | online_op | kafka 是有状态组件**不在白名单** → 只发飞书卡片交人工（正好演示边界） |
| **发版内存泄漏（capstone）** | 部署泄漏 commit 的 recommendation 镜像 | code_fix / 混合双走 | **代码修复 Agent 提 PR**（recommendation 在白名单，混合根因可先自动 `kubectl rollout restart` 止血再提 PR → `auto_remediated_and_code_fix_pr`） |
| 证据不足 | 模糊告警 | — | 低置信度降级飞书 |

**内存泄漏 capstone 是完整端到端真实闭环**——从真容器 OOM 到 代码修复 Agent 提出 PR 全程真实，是整个系统的能力验证基线。

**capstone 设计（自带可运行镜像，真实闭环）**：`recommendation` 是自带的轻量 Python 服务（纯 stdlib `http.server`，`scripts/fixtures/recommendation/`），不依赖 OTel demo 镜像：
- **真实可运行**：起 `/recommend` `/metrics` `/healthz` + **后台自驱负载线程**——容器一跑内存就自然单调上升，无需外部 load-gen。import 时不跑（`__main__` 才跑），故不影响 pytest。
- **泄漏在可被 pytest 钉死的纯逻辑**：`get_recommendations` 每次把 id 无界 `append` 到模块级 list，`test_recommendation.py::test_memory_is_bounded` 是提 PR 的 gate（泄漏版失败、有界版通过）。
- **三方一致**：`seed-github.sh` 把同一 fixture 作 GitHub"泄漏 commit"、sed 生成"基线 commit"（仅差有界 deque↔无界 list 一处 diff），保证 **GitHub 仓码 = 容器码 = 代码修复 Agent 的修复目标**。

闭环：故障诊断处置 Agent 用 `kubectl top`/`kubectl describe`（RestartCount/OOMKilled/Last State）——docker 后端为 `docker stats`/`docker inspect`——加服务 `/metrics` **实地观测**内存单调上升 → 镜像 tag + `deploys.log`（k8s 加 `kubectl rollout history`）定位发版 → `git show <sha>` 映射无界 list → 判处置 → 代码修复 Agent clone→改有界结构→build+test→提 PR。

---

## 10. 记忆 + Agentic RAG + Skill（经验积累能力）

让 Agent 从"每次从零诊断"升级为"带经验积累、能跨服务迁移相似故障模式"。

### 10.1 Agent Skills（排查手册按需加载）
4 个排查手册以 SDK 原生 Skill 形式存在：`.claude/skills/<name>/SKILL.md`（memory-oom / dependency-error / resource-cpu / queue-backlog），frontmatter 的 `description` 描述"何时用"，body 是起手式查询清单 + 判定规则。故障诊断处置 Agent 按告警类型**按需自动加载**，比一次性全塞进系统提示省 token。开关：`skills="all"` + `setting_sources=["project"]`

### 10.2 记忆（Milvus 向量库，`agent/integrations/memory.py`）
每次诊断完，**编排层**（非 Agent，保持诊断只读边界）把工单存进 Milvus：
- 向量化文本 = `summary + remediation_detail + evidence`，用 **BGE**（`BAAI/bge-small-zh-v1.5`，512 维，本地离线、ModelScope 下载，无需 key；查询侧加 BGE 指令前缀）。
- 集合 `aiops_tickets`：`vector`（HNSW/IP 索引）+ 标量字段（fingerprint/alertname/suspect_service/kind/route/summary/remediation_detail/ts）。
- 入库点：`run()` 单点 `store_ticket(report)`，覆盖全部路由出口；无诊断（去重）跳过。

### 10.3 Agentic RAG（故障诊断处置 Agent 自主检索，`agent/integrations/rag_tool.py`）
历史工单检索做成 故障诊断处置 Agent 的 **in-process MCP 工具** `search_past_incidents`（`@tool` + `create_sdk_mcp_server(name="aiops_rag")`，挂 `mcp_servers={"aiops_rag": server}`，工具名 `mcp__aiops_rag__search_past_incidents`）。**真 agentic**：Agent 调查中自主决定何时检索、自组织 query，非编排层强制注入。工具只读 Milvus，不破坏诊断只读边界。价值：跨服务语义召回（如"payment 内存泄漏"召回"recommendation 无界 list 泄漏"工单）。

### 10.4 优雅降级
`AIOPS_RAG_ENABLED=0` 或 Milvus/embedding 不可用时，`store_ticket`/`search_tickets` 静默 no-op，**核心诊断闭环不受影响**。

### 10.5 SDK 关键事实（v0.2.105）
`from claude_agent_sdk import tool, create_sdk_mcp_server`；`@tool(name, description, input_schema)` handler 是 async、收 dict、返回 `{"content":[{"type":"text","text":...}]}`。`mcp_servers` 与 `setting_sources` 完全独立、可同时用。工具名 = `mcp__<dict_key>__<tool_name>`。

---

## 11. 安全边界

- **诊断只读 + 低风险白名单**：故障诊断处置 Agent `allowed_tools=[Bash,Read,Grep,Glob]` + `guard_diagnose_ops` hook。写/变更命令默认全拦；仅命中 `remediation.py` 可枚举低风险白名单（k8s/docker 两套，锚定正则 + 目标授权）才 allow 自动执行——「能执行什么」由代码枚举硬控，不靠提示词。
- **风险分级处置**：低风险线上操作（滚动重启/迁移 Pod/抬高资源上限授权工作负载）Agent 自动执行止血、记入执行台账、事后发卡知会人工复核；高风险操作（回滚版本/redis/db/删除/改集群/缩容/动有状态组件）绝不自动执行，只发卡交人工。产出记录以台账为准而非模型自述。
- **锚定正则防注入**：白名单正则 `^…$` 全匹配，`kubectl rollout restart deploy/ad && rm -rf /` 因整体不匹配被拦，`-n foo;curl evil` 这种藏在 flag 里的注入也因命名空间限定安全字符集而匹配不上；目标须在 `ALLOWED_TARGETS`（无状态业务工作负载）内，`kubectl rollout restart deploy/kafka` 被 deny。`AIOPS_REMEDIATION_ENABLED=0` 一键退回纯只读。
- **代码修复**：代码修复 Agent 仅推特性分支 + 提 PR；master **分支保护** + PR 人工评审；`deny_online_ops` hook 禁线上变更/强推/推 master。
- **build+test 通过才提 PR**：未过降级飞书卡片。
- **低置信度转人工**：`confidence < 0.6`。
- **写工单不破坏只读**：存 Milvus 在编排层做（非 Agent）；RAG 检索工具只读。
- **成本上限**：每次 `query()` 设 max_turns / 超时 / 预算。
- **隔离**：故障诊断处置 Agent 为加载 Skill 开 `setting_sources=["project"]`（仅项目作用域）；`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`（用自建 Milvus 记忆）、独立 cwd。
- GitHub token（使用者自己的 PAT，scope 仅 `public_repo`）走环境变量注入，配合上游 master 分支保护兜底；token 只能动使用者自己的 fork + 向上游开 PR，动不了上游本体。

---

## 12. Eval（`eval/run.py`）

`python -m eval.run [--only sN] [--dry-run]`：比对 `expected.json`，记录路由/成本/降级。
- 诊断正确性：`kind` / `suspect_service` / `remediation_type`（支持 `kind_any_of`）。
- 路由正确性：`expect_route` 或 `expect_route_any_of`（内存泄漏场景接受 `code_fix_pr` 或 `online_op_and_code_fix_pr`）；低置信度正确降级。
- 修复有效性（内存泄漏场景）：`verified==true`。
- 成本：usage / cost / attempts / degraded。

---

## 13. 快速运行

```bash
# 环境
python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
modelscope download BAAI/bge-small-zh-v1.5          # BGE 模型(国内, 一次)
docker compose up -d                                 # OTel Demo + Prometheus + Jaeger + Milvus
./scripts/start-milvus.sh                            # (或单独拉 Milvus 三容器)
./scripts/seed-github.sh                             # (维护者一次)建公开上游仓 + 基线/泄漏 commit + 分支保护
# 使用者：fork 上游仓 → 建 PAT(public_repo) → 在 .env 填 GITHUB_TOKEN/UPSTREAM_OWNER/REPO/FORK_OWNER
source .env                                          # 导出 GITHUB_* 等

# 内存泄漏 capstone 完整闭环（默认 docker 后端）
./scripts/inject.sh s4                                # build 泄漏镜像 + docker run --memory=128m
# 等 1-2 分钟让内存爬升: docker stats recommendation / curl localhost:8080/metrics
python -m agent.run --alert alerts/s4.json --json-out reports/s4.json

# 线上操作 → 飞书卡片(未配 webhook 打印 stdout)
./scripts/inject.sh s2 && python -m agent.run --alert alerts/s2.json
```

**切到 k8s 后端（生产形态）**：
```bash
./scripts/k8s/start-kind.sh                          # 起 kind 集群 + metrics-server + otel-demo 命名空间
export AIOPS_BACKEND=k8s AIOPS_K8S_NAMESPACE=otel-demo
./scripts/inject.sh s4                                # build 泄漏镜像 → kind load → kubectl apply(Deployment, limit 128Mi)
# 等 1-2 分钟: kubectl top pod -l app=recommendation -n otel-demo
python -m agent.run --alert alerts/s4.json           # 观测/止血全走 kubectl；混合根因 → auto_remediated_and_code_fix_pr
```
