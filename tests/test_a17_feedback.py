"""Only tests fail-closed A17.1 pre-registration; NOT learning/product acceptance."""
import pytest
from jsonschema import Draft202012Validator
from ai_commerce.creative_scaling import feedback, learning_bridge, schemas


def test_feedback_preimplementation_is_inert():
    assert schemas.FEEDBACK_PROPOSAL_SCHEMA == "CreativeFeedbackProposal.v1"
    with pytest.raises(NotImplementedError, match="A17_1_EF02_IMPLEMENTATION_REQUIRED"):
        learning_bridge.consume_learning_advisory({"evidence": "synthetic"})
    with pytest.raises(NotImplementedError, match="A17_1_EF02_IMPLEMENTATION_REQUIRED"):
        feedback.propose_next_creative({"brief": "fixture"})


def test_feedback_schema_rejects_all_before_implementation():
    from pathlib import Path
    import json
    path = Path(__file__).resolve().parents[1] / "contracts/CreativeFeedbackProposal.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid({"pretend_product_acceptance": True})
