"""Citation normalisation and verification."""

from __future__ import annotations

import pytest

from app.modules.rule_extraction.pipeline.citations import check, normalise


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Civil Code 1947.12", "Cal. Civ. Code § 1947.12"),
        ("Cal. Civ. Code § 1947.12", "Cal. Civ. Code § 1947.12"),
        ("BMC 13.63", "Berkeley Mun. Code ch. 13.63"),
        ("M.G.L. c.186, §15B", "M.G.L. c. 186, § 15B"),
        ("803 CMR 5.00", "803 C.M.R. 5.00"),
        ("N.J.S.A. 46:8-21.2", "N.J.S.A. 46:8-21.2"),
        # Regression: a digits-only title class truncated this to "N.J.S.A. 2"
        # and merged unrelated statutes into a single rule.
        ("N.J.S.A. 2A:18-61.1", "N.J.S.A. 2A:18-61.1"),
        ("NJSA 2A:42-10.10", "N.J.S.A. 2A:42-10.10"),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_nj_titles_stay_distinct():
    """2A:18-61.1 and 2A:18-61.31 are different statutes."""
    assert normalise("N.J.S.A. 2A:18-61.1") != normalise("N.J.S.A. 2A:18-61.31")


def test_unrecognised_citation_is_returned_verbatim():
    assert normalise("Some Local Ordinance 12-34") == "Some Local Ordinance 12-34"


# -- verification -----------------------------------------------------------
def test_verified_when_section_number_appears_in_text():
    r = check(
        "Cal. Civ. Code § 1947.12",
        document_text="under Civil Code 1947.12 the cap applies",
        source_url="https://example.gov",
    )
    assert r.verified and r.section_in_text


def test_verified_from_url_when_absent_from_text():
    r = check(
        "Cal. Civ. Code § 1947.12",
        document_text="no citation here",
        source_url="https://leginfo.legislature.ca.gov/x?sectionNum=1947.12",
    )
    assert r.verified and r.derivable_from_url


def test_fabricated_citation_is_rejected():
    r = check(
        "Cal. Civ. Code § 9999.99",
        document_text="nothing relevant at all",
        source_url="https://example.gov",
    )
    assert not r.verified
    assert r.note


def test_wrong_section_is_rejected():
    r = check(
        "M.G.L. c.999, §77Z",
        document_text="chapter 186 section 15B governs deposits",
        source_url="https://example.gov",
    )
    assert not r.verified


def test_empty_citation_is_rejected():
    assert not check("", document_text="x", source_url="y").verified
