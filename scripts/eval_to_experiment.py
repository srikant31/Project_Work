"""Convert data/test_questions/eval_set.csv into the Experiments screen's JSON.

Usage (from the project root, after the documents are indexed):
    python scripts/eval_to_experiment.py

The CSV names documents by code (RC-001). The Experiments screen needs each
document's database ID, which only exists in YOUR database, so this script
looks the IDs up there and writes data/test_questions/experiment_questions.json.
Then: Experiments screen -> select all documents -> Import JSON -> that file.

Conversion rules:
  should_answer yes / correct_premise -> answerable, expected document + evidence quote
  should_answer no                    -> unsupported diagnostic (no labels allowed)
  several expected documents (C02)    -> the first one listed (the screen allows one)
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CODE = re.compile(r"([A-Z]{2}-\d{3})", re.IGNORECASE)


def document_code(document):
    """RC-001 from the document name, else from the stored file name."""
    for value in (document.get("doc_name"), Path(document.get("source_key") or "").name):
        match = CODE.match(value or "")
        if match:
            return match.group(1).upper()
    return None


def convert(rows, documents):
    """Return (questions, problems). documents: eligible experiment documents."""
    by_code, problems = {}, []
    for document in documents:
        code = document_code(document)
        if code is None:
            continue
        if code in by_code:
            problems.append(f"{code}: more than one online document uses this code; keep one online")
        by_code[code] = document
    questions = []
    for row in rows:
        if row["should_answer"] == "no":
            questions.append({"question_id": row["id"], "question": row["question"],
                              "expected_doc_id": None, "expected_source_key": None,
                              "evidence_quote": None, "answerable": False})
            continue
        codes = [code for code in row["expected_doc"].split(";") if code]
        document = by_code.get(codes[0]) if codes else None
        if document is None:
            problems.append(f"{row['id']}: document {codes[0] if codes else '(none)'} is not indexed and online; question skipped")
            continue
        if len(codes) > 1:
            problems.append(f"{row['id']}: several expected documents {codes}; scored against {codes[0]} only")
        questions.append({"question_id": row["id"], "question": row["question"],
                          "expected_doc_id": document["doc_id"], "expected_source_key": None,
                          "evidence_quote": row["key_fact"] or None, "answerable": True})
    return questions, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", default=str(ROOT / "data/test_questions/eval_set.csv"))
    parser.add_argument("--output", default=str(ROOT / "data/test_questions/experiment_questions.json"))
    args = parser.parse_args()

    from backend.database.connection import SessionLocal  # also loads .env
    from backend.models.experiment_models import ExperimentQuestion
    from backend.repositories.experiment_repository import ExperimentRepository

    documents = ExperimentRepository(SessionLocal).eligible_documents()
    if not documents:
        sys.exit("No eligible documents. Index the knowledge base (and keep it online) first.")
    with open(args.questions, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    questions, problems = convert(rows, documents)
    for question in questions:  # the same validation the Experiments screen applies
        ExperimentQuestion.model_validate(question)
    Path(args.output).write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")

    answerable = sum(q["answerable"] for q in questions)
    print(f"{len(documents)} eligible documents, {len(questions)} questions written "
          f"({answerable} answerable, {len(questions) - answerable} unsupported) -> {args.output}")
    for problem in problems:
        print("  note:", problem)
    print("Next: Experiments screen -> select ALL documents -> Import JSON -> choose that file.")


if __name__ == "__main__":
    main()
