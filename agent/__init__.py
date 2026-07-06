"""AIOps 故障诊断和修复 Agent。

一句话：把「告警」变成「根因诊断 + 处置建议」，能自动改码的就改码提 PR，
需要动线上的就发飞书卡片交给人工执行。

整体是「一个引擎（Claude Agent SDK）、跑两趟、按处置类型分流」：

    告警 → 故障诊断处置 Agent（只读排查）→ 按 remediation_type 分流：
             online_op / 低置信度 → 飞书卡片，交人工执行（HITL）
             code_fix             → 代码修复 Agent（改码）→ 提 PR
             info_only            → 直接出报告

目录分三层，方便按职责阅读：
    agent/agents/        两个 Agent 主体（业务核心）
    agent/core/          运行底座（SDK 封装 / 数据模型 / 安全 hook）
    agent/integrations/  外部系统对接（告警 / 飞书 / 记忆 / RAG）
    agent/run.py         编排入口（把上面这些串起来）
    agent/config.py      集中配置（全部可用环境变量覆盖）
"""
