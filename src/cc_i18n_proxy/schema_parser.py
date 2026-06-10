"""Classify user role content blocks per spec §4 Translation Boundary Rules."""
from __future__ import annotations

from enum import Enum
from typing import Any


class BlockKind(str, Enum):
    TRANSLATE = "translate"
    PASSTHROUGH = "passthrough"


# tuple: (kind, text_to_translate_or_None, index_or_None_for_bare_string)
# Invariant: PASSTHROUGH always has text=None; TRANSLATE always has text=str.
ClassifiedBlock = tuple[BlockKind, str | None, int | None]


def classify_user_content(content: str | list[dict[str, Any]]) -> list[ClassifiedBlock]:
    if isinstance(content, str):
        return [(BlockKind.TRANSLATE, content, None)]

    classified: list[ClassifiedBlock] = []
    for idx, block in enumerate(content):
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text.startswith("<system-reminder>"):
                classified.append((BlockKind.PASSTHROUGH, None, idx))
            else:
                classified.append((BlockKind.TRANSLATE, text, idx))
        else:
            # tool_result, image, document, anything non-text → passthrough
            classified.append((BlockKind.PASSTHROUGH, None, idx))
    return classified


def extract_translatable_texts(content: str | list[dict[str, Any]]) -> list[str]:
    return [text for kind, text, _ in classify_user_content(content)
            if kind is BlockKind.TRANSLATE and text]


def rebuild_user_content(
    original: str | list[dict[str, Any]],
    translated_map: dict[int | None, str],
) -> str | list[dict[str, Any]]:
    # translated_map keys must be idx values that classify_user_content
    # returned as TRANSLATE — caller's responsibility to enforce.
    if isinstance(original, str):
        return translated_map.get(None, original)

    result: list[dict[str, Any]] = []
    for idx, block in enumerate(original):
        if idx in translated_map:
            new_block = dict(block)
            new_block["text"] = translated_map[idx]
            result.append(new_block)
        else:
            result.append(block)
    return result
