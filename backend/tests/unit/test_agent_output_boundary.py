import pytest

from app.agent.output_boundary import PublicAnswerBoundary


@pytest.mark.parametrize(
    "content",
    [
        "让我们看工具列表，然后我需要分析 System Prompt...",
        "Self-Correction during drafting: 最终决定如下...",
        "策略调整：先搜索，再调用工具。",
        "I need to reason about the tool list.",
        "系统提示词要求我...",
        "最终决定：我将调用 search_cables。",
    ],
)
def test_private_provider_text_is_blocked(content):
    result = PublicAnswerBoundary().inspect(content)
    assert result.blocked is True
    assert content not in result.content


def test_safe_engineering_verdict_is_allowed():
    content = "正常的工程结论：similar_to 不代表可直接替换。"
    result = PublicAnswerBoundary().inspect(content)
    assert result.blocked is False
    assert result.content == content


@pytest.mark.parametrize(
    "content",
    [
        "建议实物复核当前库存数量。",
        "数量精确性标记为 False，属于演示库存。",
        "库存来源标记为 synthetic stock。",
    ],
)
def test_inventory_is_normal_business_data_without_provenance_warnings(content):
    result = PublicAnswerBoundary().inspect(content)
    assert result.blocked is True
    assert result.category == "prohibited_inventory_provenance"


def test_structured_candidates_replace_markdown_candidate_lists():
    result = PublicAnswerBoundary().inspect(
        "候选有：\n**A**\n**B**",
        entities={"material_candidates": {"items": [{"id": 1}, {"id": 2}]}},
    )
    assert result.blocked is True
    assert "**" not in result.content


def test_engineering_markdown_is_preserved_for_safe_ui_rendering():
    content = (
        "## 方案\n| 拓扑 | 损耗 |\n| --- | --- |\n| 12V→5V Buck→3.3V LDO | 0.17W |\n"
        "**输出：** 电流按 100mA 计算。"
    )
    result = PublicAnswerBoundary().inspect(
        content,
        entities={"engineering_research": {"draft": {}}},
    )

    assert result.blocked is False
    assert result.content == content


def test_power_design_markdown_is_preserved_without_dropping_model_explanation():
    content = (
        "## 比较\n| 架构 | 损耗 |\n| --- | --- |\n"
        "| 5V→3.3V LDO | 0.17W |\n**解释：** 此负载下两级方案可评估。"
    )
    result = PublicAnswerBoundary().inspect(
        content,
        entities={"power_design": {"topologies": [{"topology": "buck_ldo"}]}},
    )

    assert result.blocked is False
    assert result.content == content


def test_engineering_answer_boundary_keeps_external_html_as_untrusted_text():
    result = PublicAnswerBoundary().inspect(
        "结论：<script>alert('x')</script> 是用户可见文本。",
        entities={"engineering_research": {"draft": {}}},
    )

    assert result.blocked is False
    assert result.content == "结论：<script>alert('x')</script> 是用户可见文本。"


def test_public_business_copy_is_neutralized_without_touching_internal_ids():
    content = "秋招作品展示：Portfolio Demo v2；物料 PORT-CAN-TCAN1044。"
    result = PublicAnswerBoundary().inspect(content)
    assert result.blocked is False
    assert "秋招" not in result.content
    assert "Portfolio Demo" not in result.content
    assert "PORT-CAN-TCAN1044" in result.content


@pytest.mark.parametrize("prefix", ["业务数据：", "项目数据：", "演示数据："])
def test_public_business_copy_deletes_legacy_prefix_without_replacement(prefix):
    result = PublicAnswerBoundary().inspect(f"{prefix}编码器 · IMU · ToF")
    assert result.content == "编码器 · IMU · ToF"
    assert "业务数据" not in result.content
