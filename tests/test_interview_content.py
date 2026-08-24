"""面试讲解内容与后端节点目录的一致性测试。

保护的承诺：
1. 后端 NODE_CATALOG 里的每一种节点类型，在前端讲解（web/src/interview/stages.ts）
   里都有对应的环节讲解——新增节点类型时不允许讲解悄悄缺页；
2. 讲解里声明的系统级环节（Evidence Capsule / Eval / Block 模型）存在；
3. 环节地图（STAGE_MAP_ORDER）引用的类型都有讲解条目，不会渲染出空章节；
4. 讲解内容维持诚实标注约定（占位节点的旋钮标 effective: false 等由前端类型约束，
   这里校验关键词级的诚实声明仍然在文案中）。
"""

from __future__ import annotations

import re
from pathlib import Path

from openrag_forge.pipeline.compiler import NODE_CATALOG

STAGES_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "interview" / "stages.ts"


def _stage_keys(source: str) -> set[str]:
    # 匹配 STAGE_LESSONS 字面量里的键：`  parse_route: {` / `  _eval: {`
    return set(re.findall(r"^\s{2}(\w+):\s*\{", source, flags=re.MULTILINE))


def test_every_catalog_node_type_has_interview_lesson():
    source = STAGES_TS.read_text(encoding="utf-8")
    lesson_keys = _stage_keys(source)
    missing = sorted(set(NODE_CATALOG) - lesson_keys)
    assert not missing, f"后端节点目录新增了类型但面试讲解缺页：{missing}"


def test_system_level_lessons_exist():
    source = STAGES_TS.read_text(encoding="utf-8")
    lesson_keys = _stage_keys(source)
    for virtual in ("_block_model", "_evidence_capsule", "_eval"):
        assert virtual in lesson_keys, f"系统级环节讲解缺失：{virtual}"


def test_stage_map_only_references_existing_lessons():
    source = STAGES_TS.read_text(encoding="utf-8")
    lesson_keys = _stage_keys(source)
    map_match = re.search(r"STAGE_MAP_ORDER[^=]*=\s*\[(.*)\]", source, flags=re.DOTALL)
    assert map_match, "找不到 STAGE_MAP_ORDER"
    referenced = set(re.findall(r"'([\w]+)'", map_match.group(1)))
    referenced -= {"ingest", "query", "crosscut", "system"}
    unknown = sorted(referenced - lesson_keys)
    assert not unknown, f"环节地图引用了没有讲解的类型：{unknown}"


def test_honesty_wording_present_for_stub_nodes():
    """占位/退化节点的讲解必须包含诚实声明关键词，防止文案被改写成『已实现』。"""
    source = STAGES_TS.read_text(encoding="utf-8")
    stub_types = [node_type for node_type in NODE_CATALOG if node_type in source]
    assert stub_types
    # 关键占位节点的 liveStatus 段落必须含「占位」或「退化」措辞
    for node_type in ("reranker", "rrf_fusion", "sparse_retrieve", "intent_router", "metadata_filter", "context_builder"):
        block_match = re.search(rf"^\s{{2}}{node_type}:\s*\{{.*?^\s{{2}}\}},", source, flags=re.MULTILINE | re.DOTALL)
        assert block_match, f"找不到 {node_type} 的讲解块"
        assert ("占位" in block_match.group(0)) or ("退化" in block_match.group(0)), (
            f"{node_type} 的讲解不再包含诚实声明（占位/退化）"
        )
