---
name: resource-cpu
description: CPU / 资源型排查手册。当 CPU 飙高、延迟随流量上升（如 ad service CPU 飙高）时使用。给出 CPU 使用率、实时占用、流量对比区分流量驱动 vs 代码热点、延迟 p99 的起手式只读查询清单与判定规则。
---

# 排查手册: CPU / 资源型

适用：CPU 飙高、延迟随流量上升（如 s2 ad service CPU 飙高）。

## 起手式查询清单

1. **该服务先查有哪些指标**（Prometheus）：
   ```
   curl -s --data-urlencode 'query={service_name="<svc>"}' 'http://localhost:9090/api/v1/query'
   ```
   本环境没有 `container_cpu_*` 一类的 cAdvisor 指标；CPU 信号按语言栈落在不同指标名上——JVM 服务（如 ad）用 `jvm_cpu_recent_utilization_ratio{service_name="<svc>"}`，Python 服务用 `process_runtime_cpython_cpu_utilization_ratio{service_name="<svc>"}`，其它语言用 `process_cpu_utilization_ratio{service_name="<svc>"}`；直接用第一步查到的指标名，不要凭猜测拼。若该服务在 Prometheus 里没有任何指标，跳过 Prometheus，直接用 `docker stats`/`docker inspect` 判断。

2. **实时**：（k8s）`kubectl top pod -l app=<svc>`；（docker）`docker stats --no-stream` 看该服务 CPU。

3. **流量 vs 热点**：对比该服务的调用量（`traces_span_metrics_calls_total{service_name="<svc>"}`）。
   - 调用量同步上升 → 流量驱动，扩容/限流即可。
   - 调用量平稳但 CPU 飙高 → 代码热点（死循环/低效算法），可能 `code_fix`。

4. **延迟**：`histogram_quantile(0.99, rate(traces_span_metrics_duration_milliseconds_bucket{service_name="<svc>"}[5m]))` 看 p99 是否随之恶化。

## 判定

- 流量上涨导致 CPU 高 → **`online_op`**（抬高资源上限 / 扩副本 / 限流），`remediation_detail` 写"抬高 limits 或扩到 N 副本或加限流"。授权工作负载可 `kubectl set resources deploy/<svc> --limits=...`（docker 后端为 `docker update`）自动止血。
- 流量平稳但 CPU 高、能定位到代码热点 → `code_fix`（需高置信度）。
- 不确定 → 优先 `online_op` 先止血。
