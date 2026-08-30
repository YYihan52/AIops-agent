"""两个 Agent 的结构化输出数据模型。

整条路由都押在 故障诊断处置 Agent 输出的这份 JSON 上，所以它必须经过 schema 校验 + 重试。
用 pydantic 定义类型（好在 Python 侧做校验），同时手写一份 JSON schema 交给 SDK
强制模型按格式输出——两者要保持一致。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Kind = Literal["dependency", "resource", "deploy_regression", "config"]
RemediationType = Literal["online_op", "code_fix", "info_only"]


class TimeWindow(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class ExecutedAction(BaseModel):
    """一条已被 Agent 自动执行的低风险线上处置操作。

    重要：这个字段**不是模型自己填的**，而是编排层从「执行台账」（agent/core/remediation.py
    的 hook 在真正放行那一刻记录的）读出来回填的。所以它反映的是「代码层真的放行执行了什么」，
    不是「模型声称自己做了什么」——这正是「工具层安全控制、不靠提示词」原则在产出记录上的延续。
    """

    action: str  # 操作 ID：restart_instance / migrate_instance / scale_resources
    target: str  # 被操作的实例名
    command: str  # 实际放行执行的命令
    ts: Optional[str] = None


class Diagnosis(BaseModel):
    """故障诊断处置 Agent 的产出。经过校验；反复失败时会合成一个 confidence=0 的兜底，
    让编排层的低置信度逃生口把它交给人工。"""

    summary: str
    kind: Kind
    suspect_service: str
    # 当 remediation_type=code_fix 或 also_code_fix=true 时，若已定位到具体可疑仓库/commit/文件
    # （如通过 deploys.log 关联的 git log/show），填这三项，帮代码修复 Agent 直接对上目标；
    # 定位不到就留 None，不强填。
    suspect_repo: Optional[str] = None
    suspect_commit_hint: Optional[str] = None
    suspect_file_hint: Optional[str] = None
    remediation_type: RemediationType
    remediation_detail: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(min_length=1)
    time_window: Optional[TimeWindow] = None
    # 混合根因：online_op 负责止血，但代码也需要改才能根治。
    # 当 remediation_type=online_op 且此项为 True 时，编排层会「两条腿走路」：
    # 既发飞书卡片（回滚/重启止血），又触发 代码修复 Agent 去提一个改码 PR。
    also_code_fix: bool = False
    # 已自动执行的低风险线上处置。**不由模型填**：编排层从执行台账（hook 放行时记录）回填，
    # 所以它是「代码层真的执行了什么」的可信记录。故意不放进 DIAGNOSIS_JSON_SCHEMA。
    executed_actions: list[ExecutedAction] = Field(default_factory=list)

    @staticmethod
    def fallback(reason: str) -> "Diagnosis":
        """校验失败/触发成本上限时用的 confidence=0 兜底结果，会被路由到人工。"""
        return Diagnosis(
            summary=f"诊断未能产出可信结果: {reason}",
            kind="config",
            suspect_service="unknown",
            remediation_type="info_only",
            remediation_detail="自动诊断失败/降级，需人工介入排查。",
            confidence=0.0,
            evidence=[f"degraded: {reason}"],
        )


# 交给 SDK output_format 的 JSON schema（强制模型按此结构化输出）。
# 这里手写而不是用 model_json_schema，是为了能精确控制 additionalProperties
# 以及 "from" 别名，符合 SDK 的期望。
DIAGNOSIS_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "kind",
        "suspect_service",
        "remediation_type",
        "remediation_detail",
        "confidence",
        "evidence",
    ],
    "properties": {
        "summary": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["dependency", "resource", "deploy_regression", "config"],
        },
        "suspect_service": {"type": "string"},
        "suspect_repo": {"type": ["string", "null"]},
        "suspect_commit_hint": {"type": ["string", "null"]},
        "suspect_file_hint": {"type": ["string", "null"]},
        "remediation_type": {
            "type": "string",
            "enum": ["online_op", "code_fix", "info_only"],
        },
        "remediation_detail": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "time_window": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
        "also_code_fix": {"type": "boolean"},
    },
}


class FixResult(BaseModel):
    """代码修复 Agent 的产出。verified 是「是否提了 PR」的总闸门（只有验证通过才会提）。"""

    verified: bool
    pr_url: Optional[str] = None
    build_cmd: Optional[str] = None
    test_cmd: Optional[str] = None
    verify_log: Optional[str] = None
    changed_files: list[str] = Field(default_factory=list)
    degraded: Optional[str] = None  # null | timeout | build_failed | test_failed | ...


FIX_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verified", "changed_files"],
    "properties": {
        "verified": {"type": "boolean"},
        "pr_url": {"type": ["string", "null"]},
        "build_cmd": {"type": ["string", "null"]},
        "test_cmd": {"type": ["string", "null"]},
        "verify_log": {"type": ["string", "null"]},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "degraded": {"type": ["string", "null"]},
    },
}
