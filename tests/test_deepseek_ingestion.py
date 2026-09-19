from __future__ import annotations

import importlib.util

import pytest

from scripts.check_deepseek_ingestion import verify_deepseek_item

fixture_spec = importlib.util.spec_from_file_location(
    "check_fixture", "tests/test_check_local_ai_ingestion.py"
)
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)


def test_checker_accepts_deepseek_provider_and_complete_evidence():
    sample, item, job, note = fixture.make_fixture()
    item["metadata"]["analysis_provider"] = "deepseek"
    note = note.replace("analysis_provider: fixture", "analysis_provider: deepseek")
    result = verify_deepseek_item(item, job, sample, note, "fixture-model")
    assert result["passed"] is True
    assert result["analysis_provider"] == "deepseek"


@pytest.mark.parametrize("mutation", ["provider", "model", "mode", "coverage", "quote"])
def test_checker_rejects_provider_or_grounding_invariants(mutation):
    sample, item, job, note = fixture.make_fixture()
    item["metadata"]["analysis_provider"] = "deepseek"
    note = note.replace("analysis_provider: fixture", "analysis_provider: deepseek")
    if mutation == "provider":
        item["metadata"]["analysis_provider"] = "rules"
    elif mutation == "model":
        item["metadata"]["model"] = "another-model"
    elif mutation == "mode":
        item["metadata"]["analysis_mode"] = "rules"
    elif mutation == "coverage":
        item["metadata"]["coverage"]["chunks"][1]["start"] += 1
    else:
        item["metadata"]["evidence"][0]["quote"] = "幻觉"
    with pytest.raises(ValueError):
        verify_deepseek_item(item, job, sample, note, "fixture-model")
