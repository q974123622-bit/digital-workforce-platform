"""企微 markdown 降级转换的回归测试。

回归背景（2026-09-18）：平台会话侧放开 GFM 后，企微机器人仍把 answer 原样透传，
而企微 markdown 不支持表格与围栏代码块 —— 用户会看到裸的 ``|`` 与未闭合围栏。
"""

from app.services.wecom_markdown import to_wecom_markdown


def test_plain_text_is_returned_unchanged():
    text = "VPN 的连接方式如下，请先安装客户端。\n\n有问题随时找我。"
    assert to_wecom_markdown(text) == text


def test_empty_input_is_returned_as_is():
    assert to_wecom_markdown("") == ""
    assert to_wecom_markdown("   ") == "   "


def test_table_becomes_labelled_lines_without_pipes():
    text = (
        "当前可用的插件如下：\n"
        "\n"
        "| 插件名称 | 插件ID | 版本 |\n"
        "| --- | --- | --- |\n"
        "| 公网搜索 | internet-search | 1.0.0 |\n"
        "| 知识检索 | kb-search | 2.1.0 |\n"
    )
    converted = to_wecom_markdown(text)
    assert "|" not in converted
    assert "---" not in converted
    assert "· 插件名称：公网搜索；插件ID：internet-search；版本：1.0.0" in converted
    assert "· 插件名称：知识检索；插件ID：kb-search；版本：2.1.0" in converted
    assert "当前可用的插件如下：" in converted


def test_table_with_alignment_markers_is_detected():
    text = "| 名称 | 值 |\n|:--|--:|\n| A | 1 |"
    assert to_wecom_markdown(text) == "· 名称：A；值：1"


def test_empty_cells_are_skipped():
    text = "| 名称 | 备注 |\n| --- | --- |\n| 公网搜索 |  |"
    assert to_wecom_markdown(text) == "· 名称：公网搜索"


def test_fenced_code_block_loses_fence_and_becomes_quote():
    text = "示例：\n\n```python\nimport matplotlib.pyplot as plt\nimport numpy as np\n```\n"
    converted = to_wecom_markdown(text)
    assert "```" not in converted
    assert "> 代码（python）" in converted
    assert "> import matplotlib.pyplot as plt" in converted
    assert "> import numpy as np" in converted


def test_blank_lines_inside_code_block_are_preserved():
    text = "```bash\nset -e\n\nls -l\n```"
    converted = to_wecom_markdown(text)
    assert converted.split("\n") == ["> 代码（bash）", "> set -e", ">", "> ls -l"]


def test_unclosed_fence_is_still_converted():
    """模型漏写收尾 fence 时，剩余内容全部按代码体处理，不得残留裸 ```。"""
    text = "```python\nprint(1)\nprint(2)"
    converted = to_wecom_markdown(text)
    assert "```" not in converted
    assert converted.split("\n") == ["> 代码（python）", "> print(1)", "> print(2)"]


def test_tilde_fence_is_supported():
    text = "~~~\nplain\n~~~"
    converted = to_wecom_markdown(text)
    assert "~~~" not in converted
    assert "> plain" in converted


def test_heading_markers_are_stripped():
    text = "## 报销流程\n\n先提交申请。"
    assert to_wecom_markdown(text) == "报销流程\n\n先提交申请。"


def test_image_becomes_link():
    text = "![架构图](https://example.com/a.png)"
    assert to_wecom_markdown(text) == "[图片：架构图](https://example.com/a.png)"
    assert to_wecom_markdown("![](https://example.com/a.png)") == "[图片](https://example.com/a.png)"


def test_inline_pipe_text_is_not_treated_as_table():
    """没有分隔行的竖线只是普通文本，不能误判成表格。"""
    text = "参数 a | b 表示二选一。"
    assert to_wecom_markdown(text) == text


def test_conversion_is_idempotent():
    text = (
        "| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n\n"
        "```python\nprint(1)\n```\n\n## 标题\n"
    )
    once = to_wecom_markdown(text)
    assert to_wecom_markdown(once) == once
