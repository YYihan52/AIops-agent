"""告警接入 + 幂等去重。

当前 MVP：直接读取静态的 alerts/sN.json（生产环境这里应接 Alertmanager webhook）。
去重指纹 = hash(告警名 + 服务 + 关键 label)；同一指纹在冷却窗口内只处理一次，
用一个内存里的「指纹 -> 上次时间」字典实现，避免告警风暴反复触发 Agent 烧钱。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from agent import config

# 指纹 -> 上次处理的时间戳（epoch 秒）
_SEEN: dict[str, float] = {}


def load_alert(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def alert_fingerprint(alert: dict) -> str:
    """由告警名 + 服务 + 严重级别算一个短指纹，用于识别「同一类告警」。"""
    name = alert.get("alertname") or alert.get("name") or ""
    labels = alert.get("labels", {}) or {}
    service = alert.get("service") or labels.get("service") or labels.get("job") or ""
    severity = labels.get("severity", "")
    key = f"{name}|{service}|{severity}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def seen_recently(fingerprint: str) -> bool:
    """该指纹是否在冷却窗口内已经处理过（是则本次应跳过）。"""
    now = time.time()
    # 顺手清理掉已经过期的旧记录
    for fp in [k for k, ts in _SEEN.items() if now - ts > config.DEDUP_TTL_SECONDS]:
        _SEEN.pop(fp, None)
    last = _SEEN.get(fingerprint)
    if last is not None and now - last <= config.DEDUP_TTL_SECONDS:
        return True
    _SEEN[fingerprint] = now
    return False
