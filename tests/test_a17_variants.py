"""Only tests fail-closed A17.1 pre-registration; NOT creative product acceptance."""
import pytest
from jsonschema import Draft202012Validator
from ai_commerce.creative_scaling import variants, schemas


def test_variants_preimplementation_is_inert():
    assert schemas.VARIANT_BATCH_SCHEMA == "CreativeVariantBatch.v1"
    with pytest.raises(NotImplementedError, match="A17_1_EF02_IMPLEMENTATION_REQUIRED"):
        variants.create_variant_batch({"master": "fixture"})


def test_variants_schema_rejects_all_before_implementation():
    from pathlib import Path
    import json
    path = Path(__file__).resolve().parents[1] / "contracts/CreativeVariantBatch.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid({"id": "pretend-pass"})
