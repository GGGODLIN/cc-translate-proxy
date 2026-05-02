"""Tests for translator error classes and AnnotatedResult dataclass."""
import pytest
from dataclasses import FrozenInstanceError

from cc_i18n_proxy.translator import (
    AnnotatedResult,
    AuthError,
    ClientError,
    NetworkError,
    RateLimitError,
    ServerError,
    TranslationResult,
    TranslatorChainExhausted,
    TranslatorConfigError,
)


class TestErrorHierarchy:
    def test_retryable_errors_are_distinct_classes(self):
        assert issubclass(RateLimitError, Exception)
        assert issubclass(ServerError, Exception)
        assert issubclass(NetworkError, Exception)

    def test_fatal_errors_are_distinct_classes(self):
        assert issubclass(AuthError, Exception)
        assert issubclass(ClientError, Exception)

    def test_retryable_not_subclass_of_fatal(self):
        assert not issubclass(RateLimitError, (AuthError, ClientError))
        assert not issubclass(ServerError, (AuthError, ClientError))
        assert not issubclass(NetworkError, (AuthError, ClientError))

    def test_fatal_not_subclass_of_retryable(self):
        assert not issubclass(AuthError, (RateLimitError, ServerError, NetworkError))
        assert not issubclass(ClientError, (RateLimitError, ServerError, NetworkError))

    def test_chain_exhausted_carries_errors(self):
        errors = [{"provider": "x", "code": 429}]
        exc = TranslatorChainExhausted(errors)
        assert exc.errors == errors

    def test_config_error_carries_provider_and_cause(self):
        cause = AuthError("401")
        exc = TranslatorConfigError(
            provider="groq-llama",
            cause=cause,
            failover_attempts=["groq-other"],
            failover_errors=[{"provider": "groq-other", "code": 429}],
        )
        assert exc.provider == "groq-llama"
        assert exc.cause is cause
        assert exc.failover_attempts == ["groq-other"]
        assert exc.failover_errors == [{"provider": "groq-other", "code": 429}]


class TestAnnotatedResult:
    def test_carries_translation_and_provenance(self):
        result = TranslationResult(text="Hello", source_lang="zh", target_lang="en")
        annotated = AnnotatedResult(
            result=result,
            provider_name="groq-llama",
            failover_attempts=[],
            failover_errors=[],
        )
        assert annotated.result.text == "Hello"
        assert annotated.provider_name == "groq-llama"

    def test_failover_history_defaults_empty(self):
        result = TranslationResult(text="Hi", source_lang="en", target_lang="zh")
        annotated = AnnotatedResult(result=result, provider_name="x")
        assert annotated.failover_attempts == []
        assert annotated.failover_errors == []

    def test_is_frozen(self):
        result = TranslationResult(text="x", source_lang="zh", target_lang="en")
        annotated = AnnotatedResult(result=result, provider_name="p")
        with pytest.raises(FrozenInstanceError):
            annotated.provider_name = "other"  # type: ignore[misc]
