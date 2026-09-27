from types import SimpleNamespace

import pytest

import evals.vendor_source_assertions as source_assertions


def _pages(page_four: str):
    return [SimpleNamespace(extract_text=lambda: "") for _ in range(3)] + [
        SimpleNamespace(extract_text=lambda: page_four)
    ]


def test_lm5164_source_assertion_reads_physical_page_four(monkeypatch):
    page_four = (
        "5.1 Absolute Maximum Ratings Input voltage VIN to GND –0.3 100 V "
        "5.3 Recommended Operating Conditions VIN Input voltage 6 100 V"
    )
    monkeypatch.setattr(
        source_assertions,
        "PdfReader",
        lambda _path: SimpleNamespace(pages=_pages(page_four)),
    )

    result = source_assertions.verify_vendor_source_facts(
        source_assertions.Path("lm5164.pdf"), "lm5164.pdf"
    )

    assert result["status"] == "PASS"
    assert result["facts"]["recommended_vin_v"] == {"min": 6.0, "max": 100.0}


def test_lm5164_source_assertion_rejects_old_claim(monkeypatch):
    page_four = "5.1 Absolute Maximum Ratings VIN to GND -0.3 100 V"
    monkeypatch.setattr(
        source_assertions,
        "PdfReader",
        lambda _path: SimpleNamespace(pages=_pages(page_four)),
    )

    with pytest.raises(AssertionError, match="recommended_operating_section"):
        source_assertions.verify_vendor_source_facts(
            source_assertions.Path("lm5164.pdf"), "lm5164.pdf"
        )
