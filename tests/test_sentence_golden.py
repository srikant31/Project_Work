"""The plain-sentence check against the REAL corpus and the golden questions.

For every answerable golden question, the quote is the complete sentence or
table row that holds the key fact (exactly what the quote checker accepts),
taken from the text the app extracts from the corpus documents. Then:
  1. a faithful plain-English answer must be ACCEPTED (no needless fallback);
  2. the same answer with one number changed must be REJECTED;
  3. misleading-premise answers that repeat the false premise must be REJECTED.
This tests the checker only; whether the model writes such sentences is
measured by scripts/run_eval.py --answer-style sentence.
"""
import csv
import re
from functools import lru_cache
from pathlib import Path

import pytest

from backend.services.grounding_service import check_sentence, quote_boundary_offsets

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/documents/ABC-CORP-helpdesk-corpus"
GOLDEN = ROOT / "data/test_questions/eval_set.csv"
pytestmark = pytest.mark.skipif(not CORPUS.is_dir() or not GOLDEN.is_file(), reason="corpus not present")

# A faithful plain answer for each answerable question, using only the quoted facts.
FAITHFUL = {
    "D01": "Payroll records are kept for 7 years from the end of the tax year.",
    "D02": "The VPN idle timeout is 30 minutes.",
    "D03": "AUTH-403 means your account is locked; wait 30 minutes or call the Service Desk.",
    "D04": "For a P1 ticket the first response time is 15 minutes, and the resolution target is 4 hours.",
    "D05": "Your account locks after five consecutive failed attempts and unlocks automatically after 30 minutes.",
    "D06": "A temporary one-time password is valid for 45 minutes.",
    "D07": "You must submit an expense within 30 days of the expense date.",
    "D08": "No. For expenses below $50 a receipt is not required, but you must still enter the expense "
           "with a description and business purpose.",
    "D09": "The hotel limit is $350 per night in high-cost markets.",
    "D10": "Your mailbox limit is 50 GB.",
    "D11": "Deleted items stay in your Deleted Items folder for 30 days.",
    "D12": "Purchase requisitions from $501 to $5,000 need approval from a Manager and a Director.",
    "D13": "To be eligible for benefits you must be scheduled to work 30 or more hours per week.",
    "D14": "Report a workplace incident within 24 hours, or immediately if someone was injured.",
    "D15": "A departing employee's email account is kept for 90 days after separation, then deleted "
           "unless it is under legal hold or a business need.",
    "P01": "Your account unlocks automatically after 30 minutes.",
    "P02": "Submit your request at least five business days before the first day of absence.",
    "P03": "Yes. An absence of more than three consecutive business days requires a medical certificate.",
    "P04": "No. Forwarding company email to a personal account is prohibited.",
    "P05": "Enable automatic screen lock at 5 minutes of inactivity.",
    "P06": "Active data exfiltration is a SEV-1 Critical incident.",
    "P07": "No. Personal charges are not permitted on the company card and must be repaid within 14 days.",
    "P08": "Client entertainment meals are limited to $150 per person, with prior approval from a "
           "Vice President or above.",
    "T01": "A badge replacement needs no approval and is delivered within 1 business day.",
    "T02": "A manager's corporate card limit is $10,000.",
    "T03": "Access logs are kept for 1 year from the log date.",
    "T04": "Restricted data must be sent by encrypted email with recipient verification or approved secure "
           "file transfer, never by standard email.",
    "T05": "NET-310 means the concurrent session limit was reached; disconnect another device.",
    "T06": "Board minutes are kept permanently.",
    "C01": "A P2 issue needs a first response within 30 minutes, with a resolution target of 8 business hours.",
    "C02": "The documents disagree: RC-001 keeps employee expense records for 5 years, while FI-001 says "
           "seven years.",
    "C03": "The documents disagree: IT-005 gives a VPN session timeout of 8 hours, while SD-003 says 12 hours.",
    "C04": "The documents disagree: IT-002 sets a minimum length of 14 characters, while IT-001 says 12 "
           "characters.",
    "C05": "A High severity phishing report must be handled within 30 minutes.",
    "M01": "Payroll records are actually kept for 7 years from the end of the tax year.",
    "M02": "VPN sessions do time out: the session timeout is 8 hours.",
    "M03": "The individual meal limit is $75 per day.",
}
# Answers that accept the question's false premise must never be shown.
PREMISE = {
    "M01": "Payroll records are only kept for 2 years.",
    "M02": "VPN sessions never time out, so you can stay connected overnight.",
    "M03": "The daily allowance for individual meals is $1000.",
}


@lru_cache(maxsize=None)
def _document(code):
    """The text the app indexes. The test suite defaults PDFs to pypdf; the app
    uses pymupdf4llm (tables keep their | cells), so load with that here."""
    import os
    from backend.utils.loaders import load_document
    path = next(p for p in CORPUS.iterdir() if p.name.startswith(code + "_"))
    saved = os.environ.get("PDF_TEXT_EXTRACTOR")
    os.environ["PDF_TEXT_EXTRACTOR"] = "pymupdf4llm"
    try:
        return path.stem, load_document(path).source_text
    finally:
        if saved is None:
            os.environ.pop("PDF_TEXT_EXTRACTOR", None)
        else:
            os.environ["PDF_TEXT_EXTRACTOR"] = saved


def _normalized(text):
    """Same comparison as the grader: no pipes, bold marks, <br> or extra spaces."""
    kept, index = [], []
    for i, ch in enumerate(text):
        if ch in "|*":
            ch = " "
        if ch.isspace() and kept and kept[-1] == " ":
            continue
        kept.append(" " if ch.isspace() else ch.lower()); index.append(i)
    return "".join(kept), index


def _unit(code, fact):
    """The complete sentence/row of the document that contains the fact."""
    name, text = _document(code)
    plain, index = _normalized(re.sub(r"<br\s*/?>", lambda m: " " * len(m.group()), text, flags=re.I))
    target = _normalized(fact)[0].strip()
    found = plain.find(target)
    assert found >= 0, f"{code}: key fact not in the extracted text"
    start, end = index[found], index[found + len(target) - 1] + 1
    bounds = quote_boundary_offsets(text)
    unit_start = max(s for s in bounds["quote_start_offsets"] if s <= start)
    unit_end = min(e for e in bounds["quote_end_offsets"] if e >= end)
    return {"doc_id": code, "doc_name": name, "quote": text[unit_start:unit_end]}


def _golden():
    with GOLDEN.open(encoding="utf-8", newline="") as handle:
        return [r for r in csv.DictReader(handle) if r["should_answer"] != "no"]


def _citations(row):
    codes = [c for c in row["expected_doc"].split(";") if c]
    facts = [f for f in (row["key_fact"], row.get("key_fact_2", "")) if f]
    citations = []
    for fact in facts:
        for code in codes:
            try:
                citations.append(_unit(code, fact))
                break
            except AssertionError:
                continue
        else:
            pytest.fail(f"{row['id']}: '{fact}' not found in {codes}")
    return citations


ROWS = _golden() if GOLDEN.is_file() else []


def test_every_answerable_golden_question_has_a_reference_sentence():
    assert {r["id"] for r in ROWS} == set(FAITHFUL)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_a_faithful_answer_is_shown(row):
    sentence, why = check_sentence(FAITHFUL[row["id"]], _citations(row))
    assert why == "accepted", f"{row['id']}: {why}"


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_the_same_answer_with_a_changed_number_falls_back_to_the_quote(row):
    faithful = FAITHFUL[row["id"]]
    match = re.search(r"\d+", faithful.replace(",", ""))
    if match is None:
        pytest.skip("answer states no digits")
    wrong = re.sub(r"\d+", str(int(match.group()) + 1), faithful.replace(",", ""), count=1)
    assert check_sentence(wrong, _citations(row))[0] is None, wrong


@pytest.mark.parametrize("question_id", sorted(PREMISE))
def test_an_answer_that_accepts_a_false_premise_is_never_shown(question_id):
    row = next(r for r in ROWS if r["id"] == question_id)
    assert check_sentence(PREMISE[question_id], _citations(row))[0] is None


def test_a_conflict_answered_from_one_side_falls_back():
    row = next(r for r in ROWS if r["id"] == "C04")
    assert check_sentence("The minimum password length is 14 characters.", _citations(row))[1] \
        == "a_cited_source_value_is_missing"
