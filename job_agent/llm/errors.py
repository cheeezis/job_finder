"""Shared errors raised by LLM providers."""


class LLMError(RuntimeError):
    """Raised when an LLM provider cannot return a usable response."""
