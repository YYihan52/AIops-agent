"""agents —— 本项目的两个 Agent 主体（业务核心，重点阅读区）。

- diagnose.py  故障诊断处置 Agent：只读排查告警、定位根因、给出结构化诊断
- code_fix.py  代码修复 Agent：clone 代码仓 → 改码 → build+test → 提 PR
- prompts.py   两个 Agent 的系统提示词与用户提示词
"""
