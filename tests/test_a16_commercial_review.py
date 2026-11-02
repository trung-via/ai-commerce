"""A16 pre-executor sentinel; no real/synthetic commerce totals accepted."""
import pytest
from ai_commerce.settlement import review, schemas

def test_a16_commercial_review_not_implemented_yet():
    assert schemas.REVIEW_SCHEMA == "CommercialReview.v1"
    with pytest.raises(NotImplementedError, match="A16_EF02_IMPLEMENTATION_REQUIRED"):
        review.commercial_review({"settled": True})
