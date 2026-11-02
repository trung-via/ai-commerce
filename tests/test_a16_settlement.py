"""Only validates inert A16 pre-implementation placeholder, not product AC."""
import pytest
from ai_commerce.settlement import evidence, ledger, schemas

def test_a16_settlement_not_implemented_yet():
    assert schemas.SETTLEMENT_SCHEMA == "SettlementEvidence.v1"
    with pytest.raises(NotImplementedError, match="A16_EF02_IMPLEMENTATION_REQUIRED"):
        evidence.admit_settlement({"status": "SETTLED"})
    with pytest.raises(NotImplementedError, match="A16_EF02_IMPLEMENTATION_REQUIRED"):
        ledger.append_order({"order": "fixture"})
