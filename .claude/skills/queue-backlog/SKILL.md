---
name: queue-backlog
description: 队列积压 / 消费延迟排查手册。当 Kafka 等队列消费延迟、积压（如 kafkaQueueProblems）时使用。给出消费 lag、生产vs消费速率对比、消费者副本健康、消费者日志的起手式只读查询清单与判定规则。
---

# 排查手册: 队列积压 / 消费延迟

适用：Kafka 等队列消费延迟、积压（如 s3 kafkaQueueProblems）。

## 起手式查询清单

1. **消费 lag**（Prometheus）：
   ```
   curl -s 'http://localhost:9090/api/v1/query?query=kafka_consumergroup_lag'
   ```
   或 broker/topic 堆积指标。

2. **生产 vs 消费速率对比**：
   ```
   curl -s 'http://localhost:9090/api/v1/query?query=rate(kafka_topic_partition_current_offset[5m])'
   curl -s 'http://localhost:9090/api/v1/query?query=rate(kafka_consumergroup_current_offset[5m])'
   ```
   生产速率持续大于消费速率 → 积压会单调增长。

3. **消费者副本数 / 健康**：（k8s）`kubectl get pod -l app=<consumer-svc>`；（docker）`docker compose ps` 看消费者实例数与状态。

4. **消费者日志**：（k8s）`kubectl logs -l app=<consumer-svc> --tail 200`；（docker）`docker compose logs <consumer-svc> --tail 200` 看是否报错/卡住。

## 判定

- 消费速率跟不上生产、消费者健康 → **`online_op`**（扩消费者副本 / 提高并发），`remediation_detail` 写"扩 consumer 到 N"。**注意** Kafka 本身是有状态组件、不在自动处置白名单里——扩容 kafka/动 broker 只发飞书卡片交人工（正好演示白名单边界）。
- 消费者频繁报错/卡死且根因在代码 → `code_fix`（需高置信度）。
