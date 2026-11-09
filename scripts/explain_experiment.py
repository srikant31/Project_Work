"""Turn experiment reports into a JSON a person can read and point at.

Usage (from the project root):
    python scripts/explain_experiment.py results/experiment1_vector.json results/experiment1_hybrid.json

Writes results/experiment1_readable.json with two views:

  by_setting   - every setting (mode, chunk size, overlap, top-k): its scores in
                 words, then every question with a verdict and the exact chunks
                 that were retrieved (document, page, section, chunk number,
                 character position, scores, text, and whether the chunk holds
                 the answer).
  by_question  - every question across all settings: which settings found the
                 answer and which did not. Questions missed everywhere point to a
                 document or question problem, not a setting problem.

The input reports are not changed.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_CHARS = 400


def normalize(text):
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = re.sub(r"[|*]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def doc_code(name):
    match = re.match(r"\s*([A-Z]{2}-\d{3})", name or "", re.I)
    return match.group(1).upper() if match else (name or "?")


def setting_name(result):
    return (f"{result['retrieval_mode']} | chunk {result['chunk_size']} / overlap {result['overlap']}"
            f" | top-{result['top_k']}")


def pct(value, total):
    return f"{value}/{total} ({round(100 * value / total)}%)" if total else "n/a"


def chunk_view(hit, expected_doc_id, quote):
    text = hit.get("text") or ""
    from_expected = str(hit.get("doc_id")) == str(expected_doc_id) if expected_doc_id else None
    holds_answer = bool(quote) and normalize(quote) in normalize(text)
    return {
        "rank": hit.get("rank"),
        "document": doc_code(hit.get("doc_name")),
        "document_name": hit.get("doc_name"),
        "page": hit.get("page_number"),
        "section": hit.get("section_heading"),
        "chunk_number": hit.get("chunk_index"),
        "characters": [hit.get("start_char"), hit.get("end_char")],
        "tokens": hit.get("token_count"),
        "scores": {key: round(hit[key], 4) for key in ("similarity_score", "bm25_score", "fusion_score")
                   if isinstance(hit.get(key), (int, float))},
        "from_expected_document": from_expected,
        "holds_the_answer": holds_answer if quote else None,
        "text_preview": text[:PREVIEW_CHARS] + ("..." if len(text) > PREVIEW_CHARS else ""),
    }


def verdict(question, chunks):
    if not question.get("answerable", True):
        return "NOT SCORED - unanswerable question; refusal is tested later with the LLM"
    answer_rank = next((c["rank"] for c in chunks if c["holds_the_answer"]), None)
    document_rank = next((c["rank"] for c in chunks if c["from_expected_document"]), None)
    if question.get("evidence_hit"):
        return f"FOUND - the answer text is in the chunk at rank {answer_rank or document_rank}"
    if document_rank:
        return (f"PARTLY - right document at rank {document_rank}, but no retrieved chunk "
                "holds the full answer text")
    return "MISSED - the right document was not retrieved at all"


def explain(reports):
    by_setting, by_question = [], defaultdict(lambda: {"found_in": [], "missed_in": []})
    meta = {}
    for report in reports:
        meta.setdefault("warnings", report.get("warnings", []))
        meta.setdefault("metric_definitions", report.get("metric_notes", {}))
        meta.setdefault("documents", len(report.get("corpus", [])))
        for result in report["results"]:
            name = setting_name(result)
            questions = result.get("questions", [])
            answerable = [q for q in questions if q.get("answerable", True)]
            found = sum(bool(q.get("evidence_hit")) for q in answerable)
            right_doc = sum(bool(q.get("source_hit")) for q in answerable)
            entries = []
            for q in questions:
                quote = q.get("evidence_quote")
                chunks = [chunk_view(hit, q.get("expected_doc_id"), quote) for hit in q.get("hits", [])]
                entry = {"question_id": q.get("question_id"), "question": q.get("question"),
                         "expected_document": next((c["document"] for c in chunks if c["from_expected_document"]),
                                                   None) if q.get("expected_doc_id") else None,
                         "answer_text_we_look_for": quote,
                         "verdict": verdict(q, chunks), "retrieved_chunks": chunks}
                entries.append(entry)
                if q.get("answerable", True):
                    bucket = by_question[q.get("question_id")]
                    bucket["question"] = q.get("question")
                    bucket["answer_text_we_look_for"] = quote
                    bucket["found_in" if q.get("evidence_hit") else "missed_in"].append(name)
            order = {"MISSED": 0, "PARTLY": 1, "FOUND": 2, "NOT SC": 3}
            entries.sort(key=lambda e: (order.get(e["verdict"][:6], 9), e["question_id"]))
            by_setting.append({
                "setting": name,
                "summary": {
                    "found_exact_answer": pct(found, len(answerable)),
                    "found_right_document": pct(right_doc, len(answerable)),
                    "rank_of_right_document_mrr": round(result.get("mean_reciprocal_rank") or 0, 2),
                    "useful_share_of_chunks": round(result.get("strict_precision_at_k") or 0, 2),
                    "chunks_in_index": result.get("chunk_count"),
                    "search_time_ms": round(result.get("mean_latency_ms") or 0, 1),
                    "missed_questions": [e["question_id"] for e in entries if e["verdict"].startswith("MISSED")],
                    "partly_found_questions": [e["question_id"] for e in entries if e["verdict"].startswith("PARTLY")],
                },
                "questions": entries,
            })
    by_setting.sort(key=lambda s: (-int(s["summary"]["found_exact_answer"].split("/")[0]), s["setting"]))
    total = len(by_setting)
    questions = []
    for qid, info in sorted(by_question.items(), key=lambda item: (len(item[1]["found_in"]), item[0])):
        found = len(info["found_in"])
        questions.append({"question_id": qid, "question": info["question"],
                          "answer_text_we_look_for": info["answer_text_we_look_for"],
                          "found_in_settings": f"{found}/{total}",
                          "pattern": ("missed by every setting - check the question or the document"
                                      if found == 0 else "found by every setting" if found == total
                                      else "depends on the setting"),
                          "missed_in": info["missed_in"]})
    return {
        "how_to_read": {
            "by_setting": "Settings sorted best first. Inside each, questions sorted MISSED, PARTLY, FOUND. "
                          "Every retrieved chunk shows its document, page, section, chunk number and "
                          "character position, so you can open the document and find it.",
            "by_question": "Questions sorted from hardest to easiest across all settings.",
            "holds_the_answer": "True when the chunk contains the full answer text (ignoring table pipes, bold and spacing).",
            "scores": "similarity_score = meaning match (cosine, -1 to 1); bm25_score = keyword match; "
                      "fusion_score = the combined hybrid rank score. Higher is better.",
        },
        "documents_searched": meta.get("documents"),
        "warnings": meta.get("warnings", []),
        "metric_definitions": meta.get("metric_definitions", {}),
        "by_setting": by_setting,
        "by_question": questions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", help="experiment report JSON files")
    parser.add_argument("--output", default=str(ROOT / "results" / "experiment1_readable.json"))
    args = parser.parse_args()
    reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.reports]
    readable = explain(reports)
    Path(args.output).write_text(json.dumps(readable, indent=2, ensure_ascii=False), encoding="utf-8")
    best = readable["by_setting"][0]
    print(f"{len(readable['by_setting'])} settings, {len(readable['by_question'])} scored questions -> {args.output}")
    print(f"Best: {best['setting']}  found {best['summary']['found_exact_answer']}")
    hardest = [q["question_id"] for q in readable["by_question"] if q["pattern"].startswith("missed by every")]
    if hardest:
        print("Missed by every setting:", ", ".join(hardest))


if __name__ == "__main__":
    sys.exit(main())
