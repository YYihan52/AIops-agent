# AIOps 根因诊断 Agent — 技术方案（实现版 v3）

> 纯实现导向工程蓝图，目标：**优雅、简洁、快速**地实现可运行 MVP。不涉及演示/示例内容。
> v3 变化：**去掉 LangGraph**，用 **Claude Agent SDK 单引擎串行跑两趟**；按"解决方案类型"路由——**线上操作→飞书卡片(HITL)结束**，**代码 bug→第二个 Agent 提 PR**。

---

## 1. 目标

```
告警 → 【诊断 Agent(只读)】定位根因 + 给出解决方案
        ├─ 解决方案=线上操作(扩容/重启/回滚/改配置) → 发飞书卡片通知人处理 → 结束(不自动执行)
        └─ 根因=代码bug → 【代码修复 Agent(写)】clone仓→定位代码→建分支→改→提PR → 结束
```
差异化：打通 **infra 根因 → 解决方案分流 →（代码类）代码行级根因 + 自动 PR** 的闭环；线上操作一律人工把关。

---

## 2. 核心设计原则

1. **不自封装数据工具**：有现成 CLI 的(kubectl/helm/git/docker/curl…)直接给 **bash + 凭证**，Agent 像 Claude Code 自己查。几乎不写工具封装/mock/fixtures。
2. **真实 demo 栈**：docker-compose 起真服务 + 真可观测 + 真 Git，bash+CLI 有真东西可连；"demo vs 生产"只是换凭证。
3. **单引擎、两趟、按方案类型分流**：用 Claude Agent SDK 跑诊断(只读)；据解决方案类型决定走飞书卡片还是代码修复(写)。**不用 LangGraph**（流程线性，框架是 overkill）。
4. **安全分级**：
   - 诊断全程**只读**（只读凭证，最硬保证）。
   - **线上操作绝不自动执行**——只发飞书卡片给人，由人操作。
   - 代码修复 Agent 可**建分支 + 提 PR**，但靠 **master 分支保护 + PR 人工评审** 兜底，推不上保护分支、合不了。

---

## 3. 架构

```
 alert ─▶ ingest ─▶ 【Agent1 诊断(只读)】 ─▶ 解决方案类型?
                     bash: kubectl/docker/             │
                     curl(Prom)/git(read)/rg           │
                     产出: 根因 + 解决方案 + 类型        │
            ┌────────────────────────┬─────────────────┴──────────┐
        online_op                 code_fix                     info_only
        发飞书卡片(HITL)            【Agent2 代码修复(写)】           直接出报告
        → 结束(人来操作)           Claude SDK: clone→定位→分支→改→PR
                                   → 结束
```
- 两个执行体都用 **Claude Agent SDK `query()`**，区别只在 **权限/凭证/工作目录/系统提示**。
- 编排是十几行 **普通 async Python**（ingest + 路由 + report 是胶水，不是 Agent）。

---

## 4. 技术栈

- Python 3.11+，全程 **async**
- **`claude-agent-sdk`**：唯一 Agent 引擎（`query()`，内置 `Bash/Read/Write/Edit/Glob/Grep`）
- demo 栈：docker-compose（OTel Demo + Prometheus + Jaeger + flagd + **Gitea**）；进阶 kind+Helm 解锁真 `kubectl`
- **飞书**：自定义机器人 webhook 发消息卡片（MVP 只通知，人来操作）
- 模型：Claude 最新 Opus（SDK `model="opus"` 别名最稳，见 §13）
- **无 LangGraph / 无 LangChain**，编排用普通 Python

---

## 5. Agent 的"工具面"（bash + 现成 CLI，按 profile）

给 Agent 的是 **bash + 一份"可用 CLI 与凭证"说明**（写进系统提示），不是一堆封装工具。

| 信号 | compose 起步 | kind 进阶 |
|---|---|---|
| 容器/Pod 状态/重启/OOM | `docker ps`/`stats`/`inspect` | `kubectl get/describe/top pod`、OOM 事件 |
| 日志 | `docker compose logs <svc>` | `kubectl logs` |
| 指标 | `curl :9090/api/v1/query`(PromQL) | 同 / `kubectl top` |
| 发版历史 | 镜像 tag(含 SHA)/`deploys.log` | `kubectl rollout history`、`helm history` |
| 码仓 + PR | `git` + `curl`(Gitea API)/`tea` | 同 |
| runbook | `rg`/`grep`/`cat` 本地 `runbooks/` | 同 |

---

## 6. demo 栈（docker-compose）

- **OpenTelemetry Demo（Astronomy Shop）**：~10 微服务 + 负载生成器 + Prometheus + Grafana + Jaeger + **flagd 故障注入**
- **Gitea**：托管服务代码仓、承载 PR；**预先在 master 上开启分支保护**（让"只能提 PR、推不上 master"这一安全前提真实成立）
- `scripts/`：`seed-gitea.sh`（导入服务源码 + 为 s4 预置植入泄漏的 commit + 配置分支保护）、`inject.sh <scenario>`（打开 flagd flag / 部署 s4 bad 镜像）、`reset.sh`
- **进阶**：同套 OTel Demo 用 Helm 装 kind → `kubectl`/`helm` 全真，"k8s 内存排查 + rollout history"愿景完整

---

## 7. 编排（普通 async Python）

```python
async def run(alert: dict) -> dict:
    rc = await diagnose(alert)                 # Agent1: SDK 只读, 产出结构化结果
    if rc["remediation_type"] == "online_op":
        send_feishu_card(rc)                   # 通知人来操作, 不跑 Agent2
        return report(rc)
    if rc["remediation_type"] == "code_fix":
        repo = clone_service_repo(rc["suspect_service"])
        rc["fix"] = await code_fix(rc, cwd=repo)   # Agent2: SDK 可写(push分支+PR)
    return report(rc)                           # info_only 等直接报告
```

**Agent1 结构化产出**（用 SDK structured output 或解析 ResultMessage）：
```json
{
  "summary": "...",
  "kind": "dependency | resource | deploy_regression | config",
  "suspect_service": "cart",
  "time_window": {"from": "...", "to": "..."},
  "remediation_type": "online_op | code_fix | info_only",
  "remediation_detail": "建议给 cart 扩容到 3 副本 / 回滚到 vX / 修复内存泄漏代码",
  "confidence": 0.0,
  "evidence": ["promql ...", "kubectl ...", "log ..."]
}
```
> 判定 `remediation_type` 的原则写进 Agent1 系统提示：**任何需要动线上(扩容/重启/回滚/改配置/改集群)→ online_op；根因在代码、需改源码→ code_fix；只需告知/无需动作→ info_only。**

---

## 8. 两个 Agent 的实现（Claude Agent SDK）

### Agent1 诊断（只读）
- `allowed_tools=["Bash","Read","Grep","Glob"]`；**只读凭证**（k8s view RBAC / Prometheus 只读 / Gitea 只读）
- `PreToolUse` hook 兜底拒一切写/线上变更（`kubectl apply|delete`、`helm`、`docker rm`、`rm -rf`、`git push`…）
- 系统提示：preset `claude_code` + append（可用 CLI 清单 + 只读约束 + remediation_type 判定规则 + 结构化输出要求）
- 收口：取 `ResultMessage`（`subtype=="success"` → `result`）解析为上面 JSON

### Agent2 代码修复（写，仅在 code_fix 时）
- 输入：诊断上下文（疑似服务/接口、时间窗、可疑发版/commit 线索）+ 已 clone 的仓目录
- 行为：排查源码 → 建 bugfix 分支 → 改码 → commit → `git push` 特性分支 → 用 git+Gitea API 提 **PR**
- 权限：Gitea token 允许 **push 特性分支 + 开 PR**；**master 分支保护**挡住直接推/合，PR 必经人工评审
- 防御纵深仍保留：`disallowed_tools=["Bash(rm -rf *)","Bash(git push --force *)"]` + `PreToolUse` hook 拒线上变更命令(`kubectl`/`helm`/`docker rm`)
- 隔离：独立 `cwd`、`setting_sources=[]`、env `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`

```python
import os
from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher

async def _run_sdk(prompt, *, cwd, allowed, append_prompt, hook):
    os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    opts = ClaudeAgentOptions(
        cwd=cwd, model="opus",
        system_prompt={"type": "preset", "preset": "claude_code", "append": append_prompt},
        allowed_tools=allowed, permission_mode="acceptEdits",
        disallowed_tools=["Bash(rm -rf *)", "Bash(git push --force *)"],
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[hook])]},
        setting_sources=[],
    )
    result = None
    async for msg in query(prompt=prompt, options=opts):
        if getattr(msg, "subtype", None) == "success":
            result = msg.result            # 注意: 不要提前 break
    return result

async def code_fix(rc, cwd):
    return parse_fix(await _run_sdk(
        fix_prompt(rc), cwd=cwd,
        allowed=["Bash","Read","Write","Edit","Grep","Glob"],
        append_prompt=fix_context(rc),     # "仅修根因; 必须建分支+提PR; 不得动线上/不得合并"
        hook=deny_online_ops,
    ))
```
> `deny_online_ops`：PreToolUse hook，命中 `kubectl apply|delete`/`helm`/`docker rm`/`rm -rf`/`--force` 即 `permissionDecision:"deny"`。

---

## 9. 场景（s1–s3 走飞书卡片，s4 走代码 PR，正好覆盖两条路径）

| 场景 | 触发 | 诊断结论 | remediation_type | 走向 |
|---|---|---|---|---|
| `s1` 依赖故障 | flagd `productCatalogFailure` | product-catalog GetProduct 报错，建议回滚/关 flag | online_op | **飞书卡片** |
| `s2` 资源型 | flagd `adServiceHighCpu` | ad service CPU 飙高，建议扩容/限流 | online_op | **飞书卡片** |
| `s3` 队列积压 | flagd `kafkaQueueProblems` | kafka 消费延迟，建议扩消费者 | online_op | **飞书卡片** |
| `s4` 发版内存泄漏(**capstone**) | 部署植入泄漏 commit 的镜像 | 某服务内存渐升+OOM，关联近期发版→Gitea commit→代码行 | code_fix | **Agent2 提 PR** |

**s4 设计（确定可复现）**：服务植入"每请求向全局结构无界追加"的泄漏 commit，镜像 tag=git SHA；负载下内存数分钟攀升→(k8s)OOMKilled/(compose)`docker stats` 飙高。Agent1：指标/`kubectl`(或`docker stats`)发现内存渐升→`rollout history`(或镜像 tag)定位近期发版→映射 Gitea commit→判 code_fix。Agent2：clone→读 diff 定位无界结构→改有界(LRU/上限)→push 分支→提 PR。

---

## 10. 运行 & MVP 范围

**MVP = 跑通 `s2`(线上操作→飞书卡片) 与 `s4`(代码→PR)，全真栈。**
```bash
docker compose up -d
./scripts/seed-gitea.sh
./scripts/inject.sh s2 && python -m agent.run --alert alerts/s2.json   # → 飞书群收到卡片
./scripts/inject.sh s4 && python -m agent.run --alert alerts/s4.json   # → Gitea 出现一条 PR
```
完成判定：`s2` 诊断为资源型并发出含"扩容建议"的飞书卡片、**不**改任何线上;`s4` 经内存排查→关联发版→定位 commit/代码行→建分支改码→Gitea 上出现 PR(且推不上 master)。

---

## 11. Eval（轻量）
`python -m eval.run`：比对 Agent1 `root_cause`/`remediation_type` 与 `expected.json`；s4 额外校验是否成功开 PR；记录 bash 调用数/token/耗时。

---

## 12. 安全边界
- **诊断只读**：只读凭证 + hook 拒一切写/线上变更（最硬保证）。
- **线上操作绝不自动执行**：online_op 一律只发飞书卡片，由人操作（MVP 不做"审批即自动执行"）。
- **代码修复**：只能 push 特性分支 + 提 PR；master **分支保护** + PR **人工评审合并**；hook 仍拒线上变更命令。
- **隔离**：每 Agent 独立 cwd、`setting_sources=[]`、禁 auto-memory；bash 跑在受限容器、仅挂所需凭证。

---

## 13. 实现顺序
1. `docker-compose.yml` 起 OTel Demo + Prometheus + Jaeger + Gitea；`seed-gitea.sh`(含分支保护)/`inject.sh`
2. Agent1 诊断（SDK 只读 + bash + 只读凭证 + 结构化输出 + remediation_type 判定）；跑通 `s2` 出诊断
3. 飞书 webhook 卡片 `send_feishu_card`；`s2` 端到端发卡
4. 普通 Python 编排 `run()`（ingest→Agent1→路由→report）
5. 造 `s4`：植入泄漏 commit 的镜像 + 部署脚本 + 镜像 tag↔SHA 映射
6. Agent2 代码修复（SDK 写，§8）；跑通 `s4` 到 PR
7. `eval/run.py` + 补 `s1/s3`
8. 进阶 profile：kind+Helm + 真 kubectl 验证
9. README

---

## 14. 参考 & 待确认/不确定点
- 沙箱：OpenTelemetry Demo（flagd 故障注入）；对标：HolmesGPT(CNCF)
- Claude Agent SDK 文档：python.md / permissions.md / claude-code-features.md / hooks.md（已核对）
- **MVP 默认假设（可改）**：(a) 飞书卡片**只通知**，人工执行线上操作；若以后要"卡片按钮→审批→Agent 执行扩容"另需设计（仍建议保留人确认）。(b) PR 安全性依赖 Gitea **master 分支保护**已正确配置。
- **不确定点**：最新 Opus 完整 model id 未在 SDK 文档列出，用 `"opus"` 别名；若走 cc-mirror 网关，model id 需与 `codewiz-model-options.json` 一致。
