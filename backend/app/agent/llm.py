"""The Anthropic client behind a two-method interface, so tests can script a fake."""

from typing import Protocol

import anthropic

# Smallest prefix the API will cache, per model family (shorter prefixes silently don't).
_CACHE_MINIMUMS = [
    ("claude-haiku-4-5", 4096),
    ("claude-opus-4-6", 4096),
    ("claude-opus-4-5", 4096),
    ("claude-opus-4-7", 2048),
    ("claude-opus-4-8", 1024),
    ("claude-sonnet-4-6", 1024),
    ("claude-sonnet-5", 1024),
    ("claude-opus-5", 512),
]


def cache_minimum_tokens(model: str) -> int:
    return next((n for prefix, n in _CACHE_MINIMUMS if model.startswith(prefix)), 4096)


class LLM(Protocol):
    def create(self, *, timeout: float, **params): ...

    def count_tokens(self, **params) -> int: ...


class AnthropicLLM:
    def __init__(self, client: anthropic.Anthropic | None = None):
        # One retry: a turn has a 20s budget, so repeated retries would just time out.
        self.client = client or anthropic.Anthropic(max_retries=1)

    def create(self, *, timeout: float, **params):
        return self.client.with_options(timeout=timeout).messages.create(**params)

    def count_tokens(self, **params) -> int:
        return self.client.messages.count_tokens(**params).input_tokens

    @property
    def has_credentials(self) -> bool:
        # The client resolves keys, tokens or a profile at construction but only fails
        # at request time, so check up front.
        c = self.client
        return any(getattr(c, attr, None) for attr in ("api_key", "auth_token", "credentials"))
