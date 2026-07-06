"""事件记忆 + 对历史诊断的 RAG，底层用 Milvus 向量库。

每完成一次诊断，就把它存成一条「工单」向量（由诊断摘要 + 处置 + 证据拼成文本再 embedding）。
下次来新告警时，故障诊断处置 Agent 可以通过一个 in-process MCP 工具（rag_tool.py）语义检索相似的历史工单
——这才是真正的 agentic RAG：由模型自己决定「要不要检索、检索什么」。

设计保证（贯彻项目「流程永不崩溃」的理念）：
  - 所有操作都包了异常处理：Milvus/embedding 挂了就降级为空操作，绝不把异常抛进诊断主流程。
  - config.RAG_ENABLED=0 可一键关闭整个子系统（存储 + 检索都变成空操作）。
  - 写入发生在编排层（不是由 Agent 写），从而保住 故障诊断处置 Agent 的只读边界；检索工具也是只读的。

Embedding：本地跑 BGE（BAAI/bge-small-zh-v1.5），走 sentence-transformers，离线、无需 API key。
模型是模块级单例，首次用到时才懒加载。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent import config

log = logging.getLogger("aiops.memory")

# 懒加载的单例（把重型 import / 连接句柄推迟到首次使用时）
_model = None            # sentence-transformers 模型
_collection = None       # pymilvus 的 Collection 句柄
_connected = False


# --------------------------------------------------------------------------
# Embedding（本地 BGE）
# --------------------------------------------------------------------------

def _resolve_model_path() -> str:
    """定位 embedding 模型：本地已下载就用本地路径，否则退回 HF 仓库 id。

    优先用 ModelScope 缓存（国内下载友好的路径）——一旦通过 `modelscope download`
    拉过，就能完全离线加载。
    """
    import os

    ms_cache = os.path.expanduser(
        os.path.join("~/.cache/modelscope", config.EMBEDDING_MODEL)
    )
    if os.path.isdir(ms_cache):
        return ms_cache
    return config.EMBEDDING_MODEL


def _get_model():
    """懒加载一次 BGE 模型。库/模型不可用时返回 None（调用方会降级为空操作）。"""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        path = _resolve_model_path()
        log.info("首次使用，正在加载 embedding 模型 %s", path)
        _model = SentenceTransformer(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding 模型不可用，RAG 降级关闭: %s", exc)
        _model = None
    return _model


def embed(text: str, *, is_query: bool = False) -> Optional[list[float]]:
    """用 BGE 把文本向量化。任何失败都返回 None（调用方降级为空操作）。

    BGE 检索有个技巧：**查询侧**加一句短指令前缀效果更好；入库的文档则原样 embedding。
    """
    model = _get_model()
    if model is None:
        return None
    try:
        payload = ("为这个句子生成表示以用于检索相关文章：" + text) if is_query else text
        vec = model.encode(payload, normalize_embeddings=True)
        return vec.tolist()
    except Exception as exc:  # noqa: BLE001
        log.warning("embed 失败: %s", exc)
        return None


# --------------------------------------------------------------------------
# Milvus 集合（collection）
# --------------------------------------------------------------------------

def _ensure_collection():
    """连上 Milvus 并确保工单集合存在（幂等）。

    返回一个 pymilvus Collection；Milvus/pymilvus 不可用时返回 None。
    """
    global _collection, _connected
    if _collection is not None:
        return _collection
    try:
        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        if not _connected:
            # MILVUS_URI 形如 http://localhost:19530
            uri = config.MILVUS_URI
            connections.connect(alias="default", uri=uri)
            _connected = True

        name = config.MILVUS_COLLECTION
        if utility.has_collection(name):
            _collection = Collection(name)
            _collection.load()
            return _collection

        # 集合不存在则新建：一条向量 + 一批便于展示/过滤的标量字段
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=config.EMBEDDING_DIM),
            FieldSchema(name="fingerprint", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="alertname", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="suspect_service", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="kind", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="route", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="remediation_detail", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="ts", dtype=DataType.DOUBLE),
        ]
        schema = CollectionSchema(fields, description="AIOps 历史诊断工单")
        _collection = Collection(name, schema)
        # HNSW 索引 + 内积（IP）度量；BGE 向量已归一化，内积等价于余弦相似度
        _collection.create_index(
            field_name="vector",
            index_params={"index_type": "HNSW", "metric_type": "IP",
                          "params": {"M": 8, "efConstruction": 64}},
        )
        _collection.load()
        log.info("已创建 Milvus 集合 %s (dim=%d)", name, config.EMBEDDING_DIM)
        return _collection
    except Exception as exc:  # noqa: BLE001
        log.warning("Milvus 不可用，RAG 降级关闭: %s", exc)
        _collection = None
        return None


def _ticket_text(diag: dict) -> str:
    """把一条诊断拼成用于 embedding 的「工单文本」（摘要 + 处置 + 证据）。"""
    parts = [
        diag.get("summary", ""),
        diag.get("remediation_detail", ""),
        "\n".join(diag.get("evidence", []) or []),
    ]
    return "\n".join(p for p in parts if p).strip()


def _truncate(s: str, n: int) -> str:
    return s[:n] if s else ""


# --------------------------------------------------------------------------
# 对外 API：存工单 + 检索工单
# --------------------------------------------------------------------------

def store_ticket(report: dict[str, Any]) -> None:
    """把一条诊断工单存进 Milvus。RAG 关闭 / 没有诊断 / 出错时都是空操作。"""
    if not config.RAG_ENABLED:
        return
    diag = report.get("diagnosis")
    if not diag:  # 比如 skipped_duplicate，或压根没产出诊断
        return
    try:
        vec = embed(_ticket_text(diag))
        if vec is None:
            return
        col = _ensure_collection()
        if col is None:
            return
        alert = report.get("alert", {}) or {}
        meta = report.get("meta", {}) or {}
        col.insert([{
            "vector": vec,
            "fingerprint": _truncate(meta.get("fingerprint", ""), 64),
            "alertname": _truncate(str(alert.get("alertname", "")), 256),
            "suspect_service": _truncate(diag.get("suspect_service", ""), 256),
            "kind": _truncate(diag.get("kind", ""), 64),
            "route": _truncate(report.get("route", ""), 64),
            "summary": _truncate(diag.get("summary", ""), 4096),
            "remediation_detail": _truncate(diag.get("remediation_detail", ""), 4096),
            "ts": time.time(),
        }])
        col.flush()
        log.info("已存工单: service=%s kind=%s route=%s",
                 diag.get("suspect_service"), diag.get("kind"), report.get("route"))
    except Exception as exc:  # noqa: BLE001
        log.warning("store_ticket 失败（已忽略）: %s", exc)


def search_tickets(query: str, top_k: Optional[int] = None) -> list[dict]:
    """语义检索与 query 相似的历史工单。只读，返回 dict 列表（可能为空），绝不抛异常。"""
    if not config.RAG_ENABLED:
        return []
    k = top_k or config.RAG_TOP_K
    try:
        vec = embed(query, is_query=True)
        if vec is None:
            return []
        col = _ensure_collection()
        if col is None:
            return []
        hits = col.search(
            data=[vec],
            anns_field="vector",
            param={"metric_type": "IP", "params": {"ef": 64}},
            limit=k,
            output_fields=["fingerprint", "alertname", "suspect_service", "kind",
                           "route", "summary", "remediation_detail", "ts"],
        )
        out: list[dict] = []
        for hit in hits[0]:
            e = hit.entity
            out.append({
                "score": round(float(hit.distance), 4),
                "alertname": e.get("alertname"),
                "suspect_service": e.get("suspect_service"),
                "kind": e.get("kind"),
                "route": e.get("route"),
                "summary": e.get("summary"),
                "remediation_detail": e.get("remediation_detail"),
                "ts": e.get("ts"),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("search_tickets 失败（已忽略）: %s", exc)
        return []
