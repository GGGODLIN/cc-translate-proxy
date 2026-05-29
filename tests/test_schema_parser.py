"""Tests for user role content block classification."""

from cc_i18n_proxy.schema_parser import (
    BlockKind,
    classify_user_content,
    extract_translatable_texts,
    rebuild_user_content,
)


def test_p1_bare_string_translatable():
    blocks = classify_user_content("你好")
    assert blocks == [(BlockKind.TRANSLATE, "你好", None)]


def test_p2_array_text_block_translatable():
    content = [{"type": "text", "text": "你好"}]
    blocks = classify_user_content(content)
    assert blocks == [(BlockKind.TRANSLATE, "你好", 0)]


def test_p3_tool_result_skipped():
    content = [{"type": "tool_result", "tool_use_id": "x", "content": "中文輸出"}]
    blocks = classify_user_content(content)
    assert blocks == [(BlockKind.PASSTHROUGH, None, 0)]


def test_p4_system_reminder_skipped():
    content = [{"type": "text", "text": "<system-reminder>foo</system-reminder>"}]
    blocks = classify_user_content(content)
    assert blocks == [(BlockKind.PASSTHROUGH, None, 0)]


def test_p5_mixed_blocks():
    content = [
        {"type": "tool_result", "tool_use_id": "x", "content": "raw"},
        {"type": "text", "text": "我的評論"},
        {"type": "text", "text": "<system-reminder>注意</system-reminder>"},
    ]
    blocks = classify_user_content(content)
    assert blocks == [
        (BlockKind.PASSTHROUGH, None, 0),
        (BlockKind.TRANSLATE, "我的評論", 1),
        (BlockKind.PASSTHROUGH, None, 2),
    ]


def test_extract_translatable_texts_only():
    content = [
        {"type": "text", "text": "我的評論"},
        {"type": "tool_result", "tool_use_id": "x", "content": "raw"},
        {"type": "text", "text": "另一段"},
    ]
    assert extract_translatable_texts(content) == ["我的評論", "另一段"]


def test_rebuild_user_content_replaces_translated():
    content = [
        {"type": "text", "text": "我的評論"},
        {"type": "tool_result", "tool_use_id": "x", "content": "raw"},
        {"type": "text", "text": "另一段"},
    ]
    translated_map = {0: "My comment", 2: "Another segment"}
    result = rebuild_user_content(content, translated_map)
    assert result == [
        {"type": "text", "text": "My comment"},
        {"type": "tool_result", "tool_use_id": "x", "content": "raw"},
        {"type": "text", "text": "Another segment"},
    ]


def test_rebuild_bare_string_replaces_full():
    result = rebuild_user_content("你好", {None: "Hello"})
    assert result == "Hello"
