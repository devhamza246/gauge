"""pytest integration: ``gauge_suite`` fixture runs a suite so a normal test can assert on it.

    def test_support_bot(gauge_suite):
        run = gauge_suite("gauge.yml")
        assert run.summary.pass_rate >= 0.9
"""
from __future__ import annotations

import pytest

from .runner import run_suite


@pytest.fixture
def gauge_suite():
    def _run(config: str = "gauge.yml", **kwargs):
        return run_suite(config, **kwargs)
    return _run
