---
name: queue-backlog
description: 队列积压 / 消费延迟排查手册。当 Kafka 等队列消费延迟、积压（如 kafkaQueueProblems）时使用。给出消费 lag、生产vs消费速率对比、消费者副本健康、消费者日志的起手式只读查询清单与判定规则。
---

# 排查手册: 队列积压 / 消费延迟

适用：Kafka 等队列消费延迟、积压（如 s3 kafkaQueueProblems）。

## 起手式查询清单

1. **消费 lag**（Prometheus）：
   ```
   curl -s --data-urlencode 'query=kafka_consumer_records_lag{service_name="<consumer-svc>"}' 'http://localhost:9090/api/v1/query'
   ```
   这是消费者侧按 `topic`/`partition` 分开的 lag；也可以查 broker 侧总览：
   ```
   curl -s 'http://localhost:9090/api/v1/query?query=kafka_lag_max'
   ```

2. **lag 趋势**：把上面两条换成 `query_range` 看 lag 是单调增长还是稳定。

3. **消费者副本数 / 健康**：（k8s）`kubectl get pod -l app=<consumer-svc>`；（docker）`docker ps --filter name=<consumer-svc>` 看消费者实例数与状态。

4. **消费者日志**：（k8s）`kubectl logs -l app=<consumer-svc> --tail 200`；（docker）`docker logs <consumer-svc> --tail 200` 看是否报错/卡住。

**不要** `docker exec` 进 kafka/队列容器跑 `kafka-topics.sh`/`kafka-consumer-groups.sh` 之类命令去查 topic/lag——`docker exec` 一律被 hook 拦截，跑了也是白跑一步；lag 用上面第 1 条的 Prometheus 指标就够，topic 名从告警本身的 `labels`/`topic` 字段拿。

## 判定

- 消费速率跟不上生产、消费者健康 → **`online_op`**（扩消费者副本 / 提高并发），`remediation_detail` 写"扩 consumer 到 N"。**注意** Kafka 本身是有状态组件、不在自动处置白名单里——扩容 kafka/动 broker 只发飞书卡片交人工（正好演示白名单边界）。
- 消费者频繁报错/卡死且根因在代码 → `code_fix`（需高置信度）。
