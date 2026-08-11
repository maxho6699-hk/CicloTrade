import pytest

from src.apps.worker.research_canary import run_canary, run_pass_canary, run_queue_canary
from src.apps.worker.research_executor import EQUITY_TEMPLATES


def test_short_canary_runs_every_template_but_cannot_pass_252_day_gate():
    result = run_canary()

    assert result["rows"] == 96
    assert len(result["templates"]) == 3


def test_long_canary_meets_its_declared_validation_gates():
    result = run_pass_canary()

    assert result["rows"] == 300
    assert all(result["validation"][key] for key in ("oos_passed", "walk_forward_passed", "stress_passed"))


@pytest.mark.parametrize("template_key", sorted(EQUITY_TEMPLATES))
def test_queue_canary_completes_each_allowlisted_equity_template(template_key):
    result = run_queue_canary(template_key)

    assert result == {"state": "completed", "template_key": template_key, "rows": 96}
