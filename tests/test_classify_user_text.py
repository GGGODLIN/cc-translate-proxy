"""Unit tests for prompt-source classifier (Tier (f))."""
from cc_i18n_proxy.server import _classify_user_text


def test_command_message_classified_as_command():
    assert _classify_user_text("<command-message>intl</command-message>\n<command-name>/intl</command-name>") == "command"


def test_other_slash_command_also_command():
    assert _classify_user_text("<command-message>some-other</command-message>") == "command"


def test_stop_hook_feedback_classified_as_hook():
    text = "Stop hook feedback:\n[checkpoint] 這段對話到了「告一段落」的點嗎?"
    assert _classify_user_text(text) == "hook"


def test_cmux_recap_user_stepped_away():
    text = "The user stepped away and is coming back. Recap in under 40 words, 1-2 plain sentences."
    assert _classify_user_text(text) == "recap"


def test_recap_via_user_is_coming_back_variant():
    text = "The user is coming back from idle. Recap progress."
    assert _classify_user_text(text) == "recap"


def test_recap_via_recap_in_under_substring():
    """Catch prompt variants that don't start with 'The user stepped away'."""
    text = "User returned. Recap in under 30 words, current state and next step."
    assert _classify_user_text(text) == "recap"


def test_plain_user_text_classified_as_human():
    assert _classify_user_text("我想知道現在的天氣") == "human"


def test_empty_string_classified_as_human():
    """Edge case: empty user_zh → human (fail-open)."""
    assert _classify_user_text("") == "human"


def test_long_normal_text_with_no_keyword_is_human():
    text = "把剛剛那段用你的能力出一個中文版作為對照。之後我們再變回英文回應。"
    assert _classify_user_text(text) == "human"


def test_recap_keyword_late_in_text_does_not_match():
    """Pattern check is bounded to first 200 chars."""
    text = ("a" * 250) + "The user stepped away"
    assert _classify_user_text(text) == "human"


def test_classify_entry_source_uses_explicit_field_when_present():
    """New entries carry prompt_source; classifier reads it directly."""
    from scripts.render_server import _classify_entry_source
    entry = {"user_zh": "我是一般 prompt", "prompt_source": "recap"}  # explicit overrides inline
    assert _classify_entry_source(entry) == "recap"


def test_classify_entry_source_falls_back_to_inline_for_legacy():
    """Old audit entries lack prompt_source → infer from user_zh."""
    from scripts.render_server import _classify_entry_source
    entry = {"user_zh": "<command-message>intl</command-message>"}
    assert _classify_entry_source(entry) == "command"


def test_classify_entry_source_empty_explicit_string_falls_back():
    """Empty prompt_source treated as missing (legacy default)."""
    from scripts.render_server import _classify_entry_source
    entry = {"user_zh": "Stop hook feedback: anything", "prompt_source": ""}
    assert _classify_entry_source(entry) == "hook"
