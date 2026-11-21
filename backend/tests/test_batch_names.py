"""D-147: названия партий в сделке уникальны — иначе этапы исполнения двух партий неразличимы."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.logistics import _batch_name


def _deal(*names):
    return SimpleNamespace(batches=[SimpleNamespace(id=f"b{i}", name=n) for i, n in enumerate(names)])


def test_free_name_kept_and_numbering_fills_gaps():
    assert _batch_name(_deal("Партия 1"), "Морем до Владивостока") == "Морем до Владивостока"
    assert _batch_name(_deal(), "") == "Партия 1"
    assert _batch_name(_deal("Партия 2"), "Партия 2") == "Партия 1"          # после удаления первой экран снова предлагает «Партия 2»
    assert _batch_name(_deal("Партия 1", "Партия 2"), "партия 2") == "Партия 3"


def test_own_duplicate_name_refused_but_rename_to_itself_allowed():
    with pytest.raises(HTTPException) as e:
        _batch_name(_deal("Морем"), "морем")
    assert e.value.status_code == 409
    assert _batch_name(_deal("Морем"), "Морем", own_id="b0") == "Морем"
