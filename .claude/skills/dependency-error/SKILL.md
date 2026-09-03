---
name: dependency-error
description: 依赖故障 / 5xx 错误率排查手册。当某服务错误率上升、调用下游报错（如 product-catalog GetProduct 报错）时使用。给出错误率、Jaeger 失败 trace 定位故障那一跳、服务依赖图、下游日志的起手式只读查询清单与判定规则。
---

# 排查手册: 依赖故障 / 5xx 错误率

适用：某服务错误率上升、调用下游报错（如 s1 product-catalog GetProduct 报错）。

## 起手式查询清单

告警的 `service` 字段已经是受影响服务，直接用它，不要再用全局查询去找"哪个服务错误率最高"。

1. **该服务的错误率**（Prometheus，可选，佐证量级）：
   ```
   curl -s --data-urlencode 'query=traces_span_metrics_calls_total{service_name="<svc>",status_code="STATUS_CODE_ERROR"}' 'http://localhost:9090/api/v1/query'
   ```
   `span_kind="SPAN_KIND_CLIENT"` 的记录里 `span_name` 就是报错的那次下游调用。若查询结果为空，说明该服务在 Prometheus 里没有指标，跳过这一步，直接看 Jaeger。

2. **Jaeger 查失败 trace**（依赖型根因的核心证据——定位错在调用链哪一跳）：
   ```
   curl -s "${JAEGER_URL:-http://localhost:16686}/jaeger/ui/api/services"
   curl -sg "${JAEGER_URL:-http://localhost:16686}/jaeger/ui/api/traces?service=<svc>&tags={\"error\":\"true\"}&lookback=1h&limit=20"
   ```
   在返回的 spans 里找 `error=true` 的那一跳，看它的 `operationName` 和下游 `process.serviceName`——**那个下游就是真正的故障源**。

3. **服务依赖图**：`curl -s "${JAEGER_URL:-http://localhost:16686}/jaeger/ui/api/dependencies?endTs=<ms>&lookback=3600000"` 确认上下游关系。

4. **下游状态与日志**：（k8s）`kubectl get pod -l app=<downstream-svc>` + `kubectl logs -l app=<downstream-svc> --tail 200`；（docker）`docker ps -a --filter name=<downstream-svc>` + `docker logs <downstream-svc> --tail 200`。若下游容器/工作负载根本不存在，直接判定该依赖缺失/不可达，不需要再去代码仓库或 compose 配置里找它本该在哪里定义。

## 判定

- 报错的那一跳指向某个下游服务，且该下游近期有 flag/配置变更 → **`online_op`**（关 flag / 回滚配置），`suspect_service` 填下游。
- 若下游报错根因在其源码 → 可考虑 `code_fix`，但需高置信度证据（具体代码行）。
- 链路定位不清 → 降低 confidence，倾向人工。
