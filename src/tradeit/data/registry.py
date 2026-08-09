"""Provider registry.

Adapters register by name; configuration selects by name. Nothing in the
pipeline imports a concrete vendor module, which is what keeps swapping data
vendors a configuration change rather than a refactor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tradeit.errors import ProviderError

_FACTORIES: dict[str, Callable[..., Any]] = {}


def register(name: str, factory: Callable[..., Any]) -> None:
    if name in _FACTORIES:
        raise ProviderError(f"provider {name!r} is already registered")
    _FACTORIES[name] = factory


def get_provider(name: str, **kwargs: Any) -> Any:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        available = ", ".join(sorted(_FACTORIES)) or "<none>"
        raise ProviderError(f"unknown provider {name!r}; registered: {available}") from None
    return factory(**kwargs)


def available() -> list[str]:
    return sorted(_FACTORIES)


def _register_builtins() -> None:
    from tradeit.data.providers.stooq import StooqProvider
    from tradeit.data.providers.synthetic import SyntheticProvider
    from tradeit.data.providers.tiingo import TiingoProvider

    for name, factory in (
        ("synthetic", SyntheticProvider),
        ("tiingo", TiingoProvider),
        ("stooq", StooqProvider),
    ):
        if name not in _FACTORIES:
            register(name, factory)


_register_builtins()
