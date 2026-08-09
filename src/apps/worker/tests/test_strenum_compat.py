import builtins
import importlib.util
from pathlib import Path

import pytest

from src.apps.worker import _compat
from src.apps.worker.learning_cycle import AssetClass
from src.apps.worker.mystic_editorial import EditorialState, SocialPlatform
from src.apps.worker.quant_learning import ModelState


@pytest.mark.parametrize(
    "member",
    [
        AssetClass.EQUITY,
        SocialPlatform.THREADS,
        EditorialState.REVIEW_REQUIRED,
        ModelState.PAPER_QUALIFIED,
    ],
)
def test_worker_string_enums_preserve_strenum_value_behavior(member):
    assert isinstance(member, str)
    assert str(member) == member.value
    assert f"{member}" == member.value


def test_python310_strenum_fallback_preserves_string_behavior(monkeypatch):
    original_import = builtins.__import__

    def import_without_strenum(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "enum" and "StrEnum" in fromlist:
            raise ImportError("simulated Python 3.10")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_strenum)
    spec = importlib.util.spec_from_file_location("worker_compat_python310", Path(_compat.__file__))
    assert spec and spec.loader
    fallback = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fallback)

    class State(fallback.StrEnum):
        ACTIVE = "active"

    assert isinstance(State.ACTIVE, str)
    assert str(State.ACTIVE) == "active"
    assert f"{State.ACTIVE}" == "active"
