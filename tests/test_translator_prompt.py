"""Unit tests for Tier (g) prompt split + legacy fallback."""
from cc_i18n_proxy.translator import (
    _SYSTEM_RULES,
    _USER_TEMPLATE,
    _concat_for_legacy_role,
)


def test_system_rules_mentions_persona_heuristic():
    """Sanity check the persona prompt includes key directives."""
    rules = _SYSTEM_RULES.lower()
    # Persona signal
    assert "taiwan" in rules or "繁體" in _SYSTEM_RULES
    # Anti-tofu-spangenese reference
    assert "anti-tofu-spangenese" in rules or "晶晶體" in _SYSTEM_RULES
    # Both halves of the rule
    assert "keep english" in rules
    assert "translate" in rules


def test_system_rules_includes_known_anchor_terms():
    """Persona heuristic mentions key anchor terms (token, prompt, etc.)."""
    text = _SYSTEM_RULES
    for term in ("token", "prompt", "API", "hook"):
        assert term in text, f"missing anchor term '{term}' in system rules"


def test_user_template_substitutes_variables():
    """User template renders source_name / target_name / text correctly."""
    rendered = _USER_TEMPLATE.format(
        source_name="English",
        target_name="Traditional Chinese",
        text="Hello world",
    )
    assert "English" in rendered
    assert "Traditional Chinese" in rendered
    assert "Hello world" in rendered
    # No leftover placeholder
    assert "{" not in rendered or "}" not in rendered


def test_concat_for_legacy_role_joins_with_double_newline():
    """Fallback concatenation must keep system + user separable for LLMs."""
    out = _concat_for_legacy_role("RULES", "USER INPUT")
    assert out == "RULES\n\nUSER INPUT"


def test_concat_for_legacy_role_preserves_content_unchanged():
    """No mutation of either input."""
    sys = "system text\nwith newline"
    usr = "user text"
    out = _concat_for_legacy_role(sys, usr)
    assert sys in out
    assert usr in out


def test_concat_legacy_then_split_recovers_original():
    """Sanity: round-trip via split mirrors the concat boundary."""
    sys = "System rules content"
    usr = "User input content"
    combined = _concat_for_legacy_role(sys, usr)
    parts = combined.split("\n\n", 1)
    assert parts[0] == sys
    assert parts[1] == usr
