"""Tests for `_extract_citations` — broadened to recognise both 제N조 and
chapter/Roman-numeral citation styles emitted by the 시행지침 RAG indexer.

Regression: previously the regex required literal `제\\s*\\d+\\s*조`, so chunks
labelled `[VI. 영농기록 작성 및 보관, CHAPTER 2]` were dropped — the
citation-guard then mis-fired its "low confidence" banner on perfectly cited
answers. See farm_agent.py::_extract_citations docstring for the four shapes
now supported.
"""
from __future__ import annotations

# Import the private helper directly — it is module-level and stable.
from app.api.farm_agent import _extract_citations


# ── Article (legacy 제N조) style ─────────────────────────────────────────────


def test_article_style_single_match():
    text = "근거: [공익직불 시행지침 > 제12조 (감액)]."
    out = _extract_citations(text)
    assert len(out) == 1
    assert out[0]["label"] == "공익직불 시행지침 > 제12조 (감액)"
    assert out[0]["doc"] == "공익직불 시행지침"
    assert "제12조" in out[0]["snippet"]


def test_article_style_dedupes_repeats():
    text = "[doc > 제3조] 본문 ... 다시 [doc > 제3조]."
    out = _extract_citations(text)
    assert len(out) == 1


# ── CHAPTER / Roman-numeral / numbered-section styles (NEW) ─────────────────


def test_chapter_style_recognised():
    """The exact shape from the user's failing answer."""
    text = (
        "(검색된 근거: [VI. 영농기록 작성 및 보관, CHAPTER 2], "
        "[6. 공익직불 준수사항, CHAPTER 1 > II.])"
    )
    out = _extract_citations(text)
    labels = {c["label"] for c in out}
    assert "VI. 영농기록 작성 및 보관, CHAPTER 2" in labels
    assert "6. 공익직불 준수사항, CHAPTER 1 > II." in labels
    assert len(out) == 2


def test_roman_numeral_only():
    text = "[II. 기본직불 자격요건] 참고."
    out = _extract_citations(text)
    assert len(out) == 1
    assert out[0]["label"] == "II. 기본직불 자격요건"


def test_chapter_without_roman():
    text = "근거 [농업경영체 등록정보, CHAPTER 3]"
    out = _extract_citations(text)
    assert len(out) == 1
    assert "CHAPTER 3" in out[0]["label"]


def test_mixed_styles_all_returned():
    text = (
        "[공익직불 시행지침 > 제5조 (정의)] 그리고 "
        "[VI. 영농기록 작성 및 보관, CHAPTER 2] 도 참고."
    )
    out = _extract_citations(text)
    assert len(out) == 2


# ── Negative / boundary cases ───────────────────────────────────────────────


def test_no_citations_returns_empty():
    assert _extract_citations("아무 인용도 없는 답변입니다.") == []


def test_brackets_without_citation_shape_ignored():
    # Plain markdown link / random brackets must not be matched.
    text = "[클릭](http://x) 그리고 [TODO] 그리고 [참고]"
    assert _extract_citations(text) == []


def test_label_split_doc_and_snippet():
    text = "[VI. 영농기록 작성 및 보관, CHAPTER 2]"
    out = _extract_citations(text)
    assert out[0]["doc"] == "VI. 영농기록 작성 및 보관"
    assert out[0]["snippet"] == "CHAPTER 2"
