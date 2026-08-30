"""Diagnosis schema 的向后兼容性测试。

覆盖：
1. 不带新字段（suspect_repo/suspect_commit_hint/suspect_file_hint）时，Diagnosis 仍能正常
   构造/校验/序列化——保证老数据/老调用方不受影响。
2. 带上新字段时，round-trip（model_validate -> model_dump -> model_validate）保持一致。
3. DIAGNOSIS_JSON_SCHEMA 里新增的三个属性存在、类型正确，且不在 required 列表里
   （即模型不填时也能通过结构化输出校验）。
"""
from __future__ import annotations

from agent.core.schema import DIAGNOSIS_JSON_SCHEMA, Diagnosis

BASE_DIAG_KWARGS = {
    "summary": "product-catalog 依赖下游超时",
    "kind": "dependency",
    "suspect_service": "product-catalog",
    "remediation_type": "code_fix",
    "remediation_detail": "需修复下游调用超时处理逻辑。",
    "confidence": 0.8,
    "evidence": ["promql: xxx", "trace: yyy"],
}


def test_diagnosis_without_new_fields_still_valid():
    """老路径：不填新字段，默认值应为 None，其余字段照常工作。"""
    diag = Diagnosis(**BASE_DIAG_KWARGS)

    assert diag.suspect_repo is None
    assert diag.suspect_commit_hint is None
    assert diag.suspect_file_hint is None

    dumped = diag.model_dump()
    assert dumped["suspect_repo"] is None
    assert dumped["suspect_commit_hint"] is None
    assert dumped["suspect_file_hint"] is None

    # round-trip 不丢字段
    roundtripped = Diagnosis.model_validate(dumped)
    assert roundtripped == diag


def test_diagnosis_with_new_fields_roundtrips():
    """新路径：定位到具体仓库/commit/文件时，round-trip 后数据保持一致。"""
    kwargs = {
        **BASE_DIAG_KWARGS,
        "suspect_repo": "octo-org/product-catalog",
        "suspect_commit_hint": "a1b2c3d",
        "suspect_file_hint": "src/product_catalog_server.py",
        "also_code_fix": False,
    }
    diag = Diagnosis.model_validate(kwargs)

    assert diag.suspect_repo == "octo-org/product-catalog"
    assert diag.suspect_commit_hint == "a1b2c3d"
    assert diag.suspect_file_hint == "src/product_catalog_server.py"

    dumped = diag.model_dump(by_alias=True)
    roundtripped = Diagnosis.model_validate(dumped)
    assert roundtripped == diag


def test_fallback_still_constructs_without_new_fields():
    """fallback() 兜底路径不受影响：新字段应默认落到 None。"""
    diag = Diagnosis.fallback("测试降级原因")

    assert diag.suspect_repo is None
    assert diag.suspect_commit_hint is None
    assert diag.suspect_file_hint is None
    assert diag.confidence == 0.0


def test_json_schema_has_new_optional_properties():
    """DIAGNOSIS_JSON_SCHEMA 结构完整，新增三个属性均为可选（不在 required 里）。"""
    assert DIAGNOSIS_JSON_SCHEMA["type"] == "object"
    assert DIAGNOSIS_JSON_SCHEMA["additionalProperties"] is False

    properties = DIAGNOSIS_JSON_SCHEMA["properties"]
    required = DIAGNOSIS_JSON_SCHEMA["required"]

    for field in ("suspect_repo", "suspect_commit_hint", "suspect_file_hint"):
        assert field in properties, f"{field} missing from DIAGNOSIS_JSON_SCHEMA properties"
        assert properties[field] == {"type": ["string", "null"]}
        assert field not in required, f"{field} must stay optional (not required)"

    # 老必填字段没被误改。
    assert required == [
        "summary",
        "kind",
        "suspect_service",
        "remediation_type",
        "remediation_detail",
        "confidence",
        "evidence",
    ]


def test_json_schema_is_valid_jsonschema():
    """若环境里装了 jsonschema 包，额外校验它本身是一份合法的 JSON Schema（否则跳过）。"""
    try:
        import jsonschema
    except ImportError:
        return  # jsonschema 不是本项目依赖，未安装时跳过，不算失败。

    jsonschema.Draft7Validator.check_schema(DIAGNOSIS_JSON_SCHEMA)

    # 顺手验证一份「带新字段」和一份「不带新字段」的样例都能通过。
    sample_without_new_fields = dict(BASE_DIAG_KWARGS)
    jsonschema.validate(sample_without_new_fields, DIAGNOSIS_JSON_SCHEMA)

    sample_with_new_fields = {
        **BASE_DIAG_KWARGS,
        "suspect_repo": "octo-org/product-catalog",
        "suspect_commit_hint": "a1b2c3d",
        "suspect_file_hint": "src/product_catalog_server.py",
    }
    jsonschema.validate(sample_with_new_fields, DIAGNOSIS_JSON_SCHEMA)
