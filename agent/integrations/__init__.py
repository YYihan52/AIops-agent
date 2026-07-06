"""integrations —— 与外部系统对接的适配层。

- ingest.py    告警接入 + 指纹幂等去重
- feishu.py    飞书交互卡片（线上操作/低置信度时通知人工）
- memory.py    事件记忆：诊断工单向量化存 Milvus + BGE 语义检索
- rag_tool.py  Agentic RAG 工具：让 故障诊断处置 Agent 自主检索历史相似工单
"""
