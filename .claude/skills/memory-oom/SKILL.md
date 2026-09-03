---
name: memory-oom
description: 内存泄漏 / OOM 排查手册。当告警涉及内存单调上升、OOMKilled、容器重启次数增多（如服务发版后内存渐升）时使用。给出内存趋势、实时占用、重启事件、关联发版、映射代码的起手式只读查询清单与判定规则。
---

# 排查手册: 内存泄漏 / OOM

适用：内存单调上升、OOMKilled、重启次数增多（如 s4 recommendation 服务发版后内存渐升）。

## 起手式查询清单

> 命令按后端给出：**k8s 为主**（生产形态），docker 为本地默认后端的等价写法。用哪套取决于当前环境。

1. **看内存趋势**（Prometheus）：
   - k8s（有 cAdvisor 时）：
     ```
     curl -sg 'http://localhost:9090/api/v1/query_range?query=container_memory_working_set_bytes{pod=~".*<svc>.*"}&start=<from>&end=<to>&step=30s'
     ```
   - docker（本地默认后端）：这里没有 cAdvisor 容器内存指标，`container_memory_*` 查不到数据，不要在这上面反复尝试；JVM 服务（如 ad）可用 `jvm_memory_used_bytes{service_name="<svc>"}` 的 `query_range` 看趋势，其它服务直接跳到第 2 步用 `docker stats`/`inspect` 观测。
   - 判断是否**单调上升**（泄漏特征）而非锯齿（正常 GC）。

2. **实时内存**：（k8s）`kubectl top pod -l app=<svc>`；（docker）`docker stats --no-stream`。

3. **重启 / OOM 事件**：
   - k8s：`kubectl describe pod -l app=<svc>` 看 `OOMKilled` / `Last State` / `Restart Count`。
   - docker：`docker inspect <container> --format '{{.RestartCount}} {{.State.OOMKilled}}'`

4. **关联近期发版**（内存上升通常是新代码引入）：
   - k8s：`kubectl rollout history deploy/<svc>`；镜像 tag 含 git SHA：`kubectl get deploy/<svc> -o jsonpath='{.spec.template.spec.containers[0].image}'`。
   - docker：`docker inspect <container> --format '{{.Config.Image}}'`。
   - 查 `deploys.log`：就在你当前工作目录（cwd）根下，直接 `cat deploys.log` 或 `ls` 看一眼即可拿到，不要用 `find /` 全盘搜——记录最近部署的镜像 tag↔SHA↔时间。

5. **映射到代码**：该服务的代码仓已经克隆在你当前工作目录下的 `workspace/<svc>/`（相对路径，如 `workspace/recommendation/`），直接 `cd workspace/<svc> && git log/show <sha>` 看 diff、`cat <file>`/`rg` 读源码定位可疑改动（如向模块级容器无界 append），不需要 `find`/`ls` 满世界猜它克隆在哪。**不要** `docker exec` 进容器里跑 `grep`/`cat` 看源码——会被拦截，源码直接在 `workspace/<svc>/` 本地读就行。

## 判定

- 内存单调上升 + 关联到某次发版 commit + 能在代码里看到无界增长结构 → **`code_fix`**，`suspect_service` 填该服务，`remediation_detail` 写明泄漏点。
- 发版引入的泄漏典型是**混合根因**：滚动重启能立刻止血（内存回落），但不改代码下次发版还复发 → 报 `online_op` 且置 `also_code_fix=true`（先重启授权工作负载止血、再触发修复 Agent 提 PR 根治）。
- 只看到内存高但定位不到代码/发版 → 调低 confidence，倾向 `online_op`（先重启/扩容止血）或人工。
