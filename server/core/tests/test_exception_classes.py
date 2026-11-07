"""No exception class is a frozen dataclass (#212 retro).

A context manager made with `contextlib.contextmanager` sets `__traceback__`
on an exception that passes through it, and a frozen dataclass refuses the
assignment with `FrozenInstanceError`, which then replaces the exception.
#212's `EventRefused` crashed that way in review; `SetupFailure`,
`PrefsFailure` and `ImportFailure` had the same shape. Write such a class as
`@dataclass(eq=False)`, which keeps the identity hash an exception has.
"""

import contextlib
import dataclasses
import importlib
import inspect
import pkgutil

import pytest
from django.apps import apps


def _local_modules():
    for config in apps.get_app_configs():
        if config.name.startswith("django."):
            continue
        package = importlib.import_module(config.name)
        yield package
        for found in pkgutil.walk_packages(package.__path__, f"{config.name}."):
            if ".tests" in found.name or ".migrations" in found.name:
                continue
            yield importlib.import_module(found.name)


def frozen_exception_classes(modules) -> list[str]:
    """`module.Class` for each exception class defined in `modules` that is a
    frozen dataclass."""
    return sorted(
        f"{module.__name__}.{name}"
        for module in modules
        for name, value in vars(module).items()
        if inspect.isclass(value)
        and value.__module__ == module.__name__
        and issubclass(value, BaseException)
        and dataclasses.is_dataclass(value)
        and value.__dataclass_params__.frozen
    )


def test_no_exception_class_is_a_frozen_dataclass():
    assert frozen_exception_classes(_local_modules()) == []


def test_the_check_finds_a_frozen_exception_and_passes_an_unfrozen_one():
    module = type(inspect)("example")

    @dataclasses.dataclass(frozen=True)
    class Frozen(Exception):
        code: str

    @dataclasses.dataclass(eq=False)
    class Plain(Exception):
        code: str

    for cls in (Frozen, Plain):
        cls.__module__ = module.__name__
        setattr(module, cls.__name__, cls)
    assert frozen_exception_classes([module]) == ["example.Frozen"]

    @contextlib.contextmanager
    def passes_through():
        yield

    with pytest.raises(dataclasses.FrozenInstanceError):
        with passes_through():
            raise Frozen("x")
    with pytest.raises(Plain):
        with passes_through():
            raise Plain("x")
