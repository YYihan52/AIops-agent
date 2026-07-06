"""飞书自定义机器人 webhook：对 online_op / 低置信度的诊断，推一张交互卡片给人工。

如果没配置 webhook，卡片会直接打印到 stdout——这样即使不接任何外部系统，
MVP 也能端到端跑通，方便同学们本地演示。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from agent import config
from agent.core.schema import Diagnosis

_KIND_CN = {
    "dependency": "依赖故障",
    "resource": "资源型",
    "deploy_regression": "发版回归",
    "config": "配置问题",
}
_REMEDIATION_CN = {
    "online_op": "线上操作（需人工执行）",
    "code_fix": "代码修复",
    "info_only": "仅告知",
}


def _build_card(diag: Diagnosis, routing_note: str | None) -> dict[str, Any]:
    conf_pct = f"{diag.confidence * 100:.0f}%"
    evidence_md = "\n".join(f"- {e}" for e in diag.evidence)
    header_color = "orange" if diag.confidence >= config.CONFIDENCE_THRESHOLD else "red"

    lines = [
        f"**疑似服务**：{diag.suspect_service}",
        f"**根因类型**：{_KIND_CN.get(diag.kind, diag.kind)}",
        f"**置信度**：{conf_pct}",
        f"**建议处置**：{_REMEDIATION_CN.get(diag.remediation_type, diag.remediation_type)}",
        f"**处置详情**：{diag.remediation_detail}",
    ]
    if diag.executed_actions:
        acts_md = "、".join(f"{a.action}→{a.target}" for a in diag.executed_actions)
        lines.append(f"**已自动止血**：{acts_md}（低风险操作，Agent 已执行，请复核）")
    if routing_note:
        lines.append(f"**路由说明**：{routing_note}")
    body = "\n".join(lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_color,
                "title": {"tag": "plain_text", "content": f"[AIOps] {diag.summary}"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**证据**\n{evidence_md}"},
                },
            ],
        },
    }


def send_feishu_card(diag: Diagnosis, routing_note: str | None = None) -> dict[str, Any]:
    """发送飞书卡片（未配 webhook 时改为打印）。返回 {sent, status}。"""
    card = _build_card(diag, routing_note)

    if not config.FEISHU_WEBHOOK_URL:
        print("\n===== [FEISHU CARD — webhook 未配置, 打印到 stdout] =====")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print("=========================================================\n")
        return {"sent": False, "status": "stdout (no webhook configured)"}

    data = json.dumps(card).encode("utf-8")
    req = urllib.request.Request(
        config.FEISHU_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"sent": True, "status": f"http {resp.status}"}
    except Exception as exc:  # noqa: BLE001 — webhook 发送失败绝不能拖垮主流程
        print(f"[feishu] webhook send failed: {exc}")
        return {"sent": False, "status": f"error: {exc}"}
