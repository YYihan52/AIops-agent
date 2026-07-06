---
name: resource-cpu
description: CPU / 资源型排查手册。当 CPU 飙高、延迟随流量上升（如 ad service CPU 飙高）时使用。给出 CPU 使用率、实时占用、流量对比区分流量驱动 vs 代码热点、延迟 p99 的起手式只读查询清单与判定规则。
---

# 排查手册: CPU / 资源型

适用：CPU 飙高、延迟随流量上升（如 s2 ad service CPU 飙高）。

## 起手式查询清单

1. **CPU 使用率**（Prometheus）：
   ```
   curl -s 'http://localhost:9090/api/v1/query?query=rate(container_cpu_usage_seconds_total{name=~".*adservice.*"}[5m])'
   ```

2. **实时**：（k8s）`kubectl top pod -l app=<svc>`；（docker）`docker stats --no-stream` 看该服务 CPU。

3. **流量 vs 热点**：对比请求量（`rate(app_*_requests_total[5m])`）。
   - 请求量同步上升 → 流量驱动，扩容/限流即可。
   - 请求量平稳但 CPU 飙高 → 代码热点（死循环/低效算法），可能 `code_fix`。

4. **延迟**：`histogram_quantile(0.99, rate(..._duration_seconds_bucket[5m]))` 看 p99 是否随之恶化。

## 判定

- 流量上涨导致 CPU 高 → **`online_op`**（抬高资源上限 / 扩副本 / 限流），`remediation_detail` 写"抬高 limits 或扩到 N 副本或加限流"。授权工作负载可 `kubectl set resources deploy/<svc> --limits=...`（docker 后端为 `docker update`）自动止血。
- 流量平稳但 CPU 高、能定位到代码热点 → `code_fix`（需高置信度）。
- 不确定 → 优先 `online_op` 先止血。
