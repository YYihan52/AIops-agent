"""core —— Agent 的运行底座（两个 Agent 共用的基础设施）。

- sdk_runner.py  对 Claude Agent SDK 的薄封装：跑一趟 query，带上成本/超时控制
- schema.py      结构化输出的数据模型（Diagnosis / FixResult）+ JSON schema
- hooks.py       安全 hook：诊断 Agent 禁写、修复 Agent 禁动线上
"""
