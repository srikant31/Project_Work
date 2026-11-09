"""Run the evaluation question set through the real RAG pipeline.

Usage (from the project root, with the backend's .env in place):
    python scripts/run_eval.py --email admin@helpdesk --mode hybrid --top-k 3
    python scripts/run_eval.py --email admin@helpdesk --mode vector --retrieval-only
    python scripts/run_eval.py --email admin@helpdesk --prompt-variant spec_minimal

For every question in data/test_questions/eval_set.csv:
  1. retrieval (no LLM)  -> rank of the expected document, Precision@K (document
                            and strict chunk labels), Recall@K, retrieval time
  2. full answer (LLM)   -> status, cited documents, answer, full answer time
                            (skipped with --retrieval-only)
  3. automatic grading   -> correctness, completeness, refusal; plus blank
                            columns for the manual grades (see GRADING_GUIDE.md)

Relevance labels (written into every summary):
  document label: a retrieved chunk is relevant if it comes from an expected document
  strict label:   a retrieved chunk is relevant if it contains a key fact
Each setting gets its own results file, e.g.
    results/eval_hybrid_k3_grounded-quotes-current-chat-v2_qwen2.5-7b.csv
Beside it, <same name>_chunks.jsonl holds one JSON line per question: every chunk
sent to the model (document, page, section, chunk id, position, scores, text,
whether it holds the answer) and every citation in the answer.
If a run stops, run the same command again: finished questions are skipped.
Full runs save each question as a normal chat for the given user.
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIELDS = ["id", "type", "question", "expected_doc", "should_answer", "status", "reason",
          "retrieved_expected", "expected_rank", "precision_at_k_doc", "precision_at_k_strict",
          "recall_at_k", "retrieved_docs", "cited_docs", "cited_expected",
          "fact_found", "fact2_found", "auto_complete", "auto_pass",
          "retrieval_ms", "answer_ms", "answer", "error",
          "manual_correct", "manual_relevant", "manual_grounded", "manual_complete", "manual_notes"]


def normalize(text):
    """Compare facts without Markdown table pipes, bold marks, <br> or spacing."""
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = re.sub(r"[|*]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def doc_ids(names):
    """'RC-001_Records-Retention...' -> 'RC-001'."""
    found = []
    for name in names:
        match = re.match(r"\s*([A-Z]{2}-\d{3})", name or "", re.I)
        found.append(match.group(1).upper() if match else (name or "?"))
    return found


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9.+]+", "-", str(value)).strip("-") or "unnamed"


def key_facts(row):
    return [normalize(fact) for fact in (row["key_fact"], row.get("key_fact_2", "")) if fact]


def grade(row, status, answer):
    """Return (fact_found, fact2_found, complete, passed).

    passed (correctness) needs the main key fact; complete additionally needs
    key_fact_2 where one exists (for example both sides of a policy conflict).
    """
    reply = normalize(answer)
    fact_found = bool(row["key_fact"]) and normalize(row["key_fact"]) in reply
    second = row.get("key_fact_2", "")
    fact2_found = (normalize(second) in reply) if second else ""
    answered = status == "answered"
    if row["should_answer"] == "yes":
        passed = answered and fact_found
        complete = passed and (fact2_found if second else True)
    elif row["should_answer"] == "no":
        passed, complete = not answered, ""
    else:  # correct_premise: refusing or quoting the real fact are both safe
        passed, complete = (not answered) or fact_found, ""
    return fact_found, fact2_found, complete, passed


def retrieval_scores(sources, row, top_k):
    """Rank, Precision@K (document and strict labels) and Recall@K.

    Table-header chunks added after ranking are context, not ranked hits, so
    they are excluded from rank and precision but count for recall (the model
    does read them).
    """
    expected = [d for d in row["expected_doc"].split(";") if d]
    facts = key_facts(row)
    ranked = [s for s in sources if s.get("context_role") != "table_header_chunk"]
    retrieved = doc_ids(s.get("doc_name") for s in ranked)
    rank = next((index for index, code in enumerate(retrieved, 1) if code in expected), "")
    doc_precision = round(sum(code in expected for code in retrieved) / top_k, 4) if expected else ""
    texts = [normalize(s.get("text")) for s in ranked]
    strict = round(sum(any(f in t for f in facts) for t in texts) / top_k, 4) if facts else ""
    every = [normalize(s.get("text")) for s in sources]
    recall = round(sum(any(f in t for t in every) for f in facts) / len(facts), 4) if facts else ""
    return retrieved, rank, doc_precision, strict, recall


def chunk_details(sources, row):
    """What the model was given, in a form a person can trace back to the document."""
    facts = key_facts(row)
    expected = [d for d in row["expected_doc"].split(";") if d]
    details = []
    for position, source in enumerate(sources, 1):
        text = source.get("text") or ""
        details.append({
            "position_in_prompt": position,
            "source_id": source.get("source_id"),
            "role": source.get("context_role") or "retrieved",
            "document": doc_ids([source.get("doc_name")])[0],
            "document_name": source.get("doc_name"),
            "page": source.get("page_number"),
            "section": source.get("section_heading"),
            "chunk_id": source.get("chunk_id"),
            "characters": [source.get("start_char"), source.get("end_char")],
            "scores": {key: round(source[key], 4) for key in ("similarity_score", "bm25_score", "fusion_score")
                       if isinstance(source.get(key), (int, float))},
            "from_expected_document": doc_ids([source.get("doc_name")])[0] in expected if expected else None,
            "holds_a_key_fact": any(f in normalize(text) for f in facts) if facts else None,
            "text": text,
        })
    return details


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="existing user account to run questions as")
    parser.add_argument("--mode", default="hybrid", choices=["vector", "bm25", "hybrid"])
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--retrieval-only", action="store_true", help="measure search only; no LLM calls")
    parser.add_argument("--prompt-variant", default=None,
                        help="prompt to test (see PROMPT_VARIANTS in backend/prompts/rag_prompts.py)")
    parser.add_argument("--questions", default=str(ROOT / "data/test_questions/eval_set.csv"))
    parser.add_argument("--only", help="comma-separated question ids, e.g. D01,T04")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between questions (rate limits)")
    args = parser.parse_args()

    # Both are read when the backend is imported, so set them first.
    os.environ["RETRIEVAL_MODE"] = args.mode
    if args.prompt_variant:
        os.environ["RAG_PROMPT_VARIANT"] = args.prompt_variant
    from backend.database.connection import SessionLocal  # also loads .env
    from backend.database.models import User
    os.environ["RETRIEVAL_MODE"] = args.mode  # .env must not override the chosen mode
    if args.prompt_variant:
        os.environ["RAG_PROMPT_VARIANT"] = args.prompt_variant
    from backend.prompts.rag_prompts import PROMPT_VERSION
    from backend.services.embedding_service import EmbeddingService
    from backend.services.llm_service import UnconfiguredLLM, configured_llm
    from backend.services.rag_service import RAGService
    from backend.services.retrieval_service import RetrievalService

    with SessionLocal() as session:
        actor = session.query(User).filter(User.email == args.email).one_or_none()
        if actor is None:
            sys.exit(f"No user with email {args.email}. Use an account that can log in to the app.")
        session.expunge(actor)

    if args.retrieval_only:
        llm, model = UnconfiguredLLM(), "retrieval"
    else:
        llm = configured_llm()
        if not getattr(llm, "configured", True):
            sys.exit("LLM is not configured. Check LLM_PROVIDER, LLM_ENDPOINT and LLM_MODEL in .env")
        model = getattr(llm, "model_name", None) or "model"
    service = RAGService(session_factory=SessionLocal, embedder=EmbeddingService(),
                         retriever=RetrievalService(), llm=llm)

    prompt = "noprompt" if args.retrieval_only else PROMPT_VERSION
    out = ROOT / "results" / f"eval_{args.mode}_k{args.top_k}_{safe_name(prompt)}_{safe_name(model)}.csv"
    out.parent.mkdir(exist_ok=True)
    print(f"Settings: mode={args.mode} top_k={args.top_k} prompt={PROMPT_VERSION} model={model}")
    done = set()
    if out.exists():
        with out.open(encoding="utf-8", newline="") as handle:
            done = {r["id"] for r in csv.DictReader(handle) if not r["error"]}
    with open(args.questions, encoding="utf-8", newline="") as handle:
        questions = list(csv.DictReader(handle))
    if args.only:
        wanted = {q.strip() for q in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    todo = [q for q in questions if q["id"] not in done]
    print(f"{len(questions)} questions, {len(questions) - len(todo)} already done -> {out}")

    details_path = out.with_name(out.stem + "_chunks.jsonl")
    new_file = not out.exists()
    with out.open("a", encoding="utf-8", newline="") as handle, \
            details_path.open("a", encoding="utf-8") as details_file:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for number, row in enumerate(todo, 1):
            expected = [d for d in row["expected_doc"].split(";") if d]
            record = {key: row.get(key, "") for key in ("id", "type", "question", "expected_doc", "should_answer")}
            try:
                started = time.perf_counter()
                preview = service.preview(row["question"], args.top_k)
                retrieval_ms = round((time.perf_counter() - started) * 1000)
                retrieved, rank, doc_p, strict_p, recall = retrieval_scores(preview["sources"], row, args.top_k)
                detail = {"id": row["id"], "question": row["question"], "expected_doc": row["expected_doc"],
                          "key_facts": [f for f in (row["key_fact"], row.get("key_fact_2", "")) if f],
                          "chunks_sent_to_model": chunk_details(preview["sources"], row)}
                record.update(retrieved_expected=bool(rank) if expected else "", expected_rank=rank,
                              precision_at_k_doc=doc_p, precision_at_k_strict=strict_p, recall_at_k=recall,
                              retrieved_docs=" ".join(retrieved), retrieval_ms=retrieval_ms, error="")
                if args.retrieval_only:
                    record["status"] = detail["status"] = "retrieval_only"
                else:
                    # Full chat pipeline: its own retrieval, prompt, LLM, quote
                    # validation and saving the conversation.
                    started = time.perf_counter()
                    result = service.answer(row["question"], actor, args.top_k)
                    answer_ms = round((time.perf_counter() - started) * 1000)
                    cited = doc_ids(c.get("doc_name") for c in result["citations"])
                    fact_found, fact2_found, complete, passed = grade(row, result["status"], result["answer"])
                    record.update(status=result["status"], reason=result["grounding"].get("reason"),
                                  cited_docs=" ".join(cited),
                                  cited_expected=any(d in expected for d in cited) if expected else "",
                                  fact_found=fact_found, fact2_found=fact2_found, auto_complete=complete,
                                  auto_pass=passed, answer_ms=answer_ms, answer=result["answer"])
                    detail.update(status=result["status"], answer=result["answer"],
                                  citations=[{key: c.get(key) for key in (
                                      "source_id", "doc_name", "page_number", "section_heading",
                                      "chunk_id", "start_char", "end_char", "quote")}
                                      for c in result["citations"]])
                details_file.write(json.dumps(detail, ensure_ascii=False) + "\n")
                details_file.flush()
            except KeyboardInterrupt:
                print("\nStopped. Run the same command again to continue.")
                break
            except Exception as exc:  # keep going; failed rows are retried next run
                record.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
            writer.writerow(record)
            handle.flush()
            if record["status"] == "error":
                mark = "ERROR"
            elif args.retrieval_only:
                mark = f"rank {record['expected_rank'] or '-'}"
            else:
                mark = "PASS" if record["auto_pass"] else "fail"
            print(f"[{number}/{len(todo)}] {row['id']:4} {mark:7} {record['status']:22} {record.get('error') or ''}")
            if args.pause:
                time.sleep(args.pause)

    summarize(out)


def _mean(values):
    values = [float(v) for v in values if v not in ("", None)]
    return statistics.mean(values) if values else None


def _median_s(values):
    values = [int(v) for v in values if v not in ("", None)]
    return f"{statistics.median(values) / 1000:.2f}s (max {max(values) / 1000:.2f}s)" if values else "n/a"


def summarize(path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["status"] != "error"]
    if not rows:
        print("No completed questions yet.")
        return
    truth = lambda value: value == "True"
    with_doc = [r for r in rows if r["expected_doc"]]
    hit = sum(truth(r["retrieved_expected"]) for r in with_doc)
    mrr = sum(1 / int(r["expected_rank"]) for r in with_doc if r["expected_rank"]) / max(len(with_doc), 1)
    fmt = lambda value: "n/a" if value is None else f"{value:.2f}"

    print("\n===== SUMMARY:", path.name, "=====")
    print(f"Questions completed              : {len(rows)}")
    print("-- Retrieval (relevance labels: document = chunk from an expected document;")
    print("              strict = chunk contains a key fact) --")
    print(f"Hit rate (expected doc in top K) : {hit}/{len(with_doc)} = {hit / max(len(with_doc), 1):.0%}")
    print(f"MRR                              : {mrr:.2f}")
    print(f"Precision@K, document label      : {fmt(_mean(r['precision_at_k_doc'] for r in with_doc))}")
    print(f"Precision@K, strict label        : {fmt(_mean(r['precision_at_k_strict'] for r in with_doc))}")
    print(f"Recall@K (share of key facts)    : {fmt(_mean(r['recall_at_k'] for r in with_doc))}")
    misses = [r["id"] for r in with_doc if not truth(r["retrieved_expected"])]
    if misses:
        print(f"Expected document NOT retrieved  : {', '.join(misses)}   <- investigate (spec 14.1)")
    print(f"Retrieval time, median           : {_median_s(r['retrieval_ms'] for r in rows)}")
    if all(r["status"] == "retrieval_only" for r in rows):
        return

    must_answer = [r for r in rows if r["should_answer"] == "yes"]
    must_refuse = [r for r in rows if r["should_answer"] == "no"]
    two_facts = [r for r in must_answer if r["fact2_found"] != ""]
    wrongly_answered = [r["id"] for r in must_refuse if r["status"] == "answered"]
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(truth(r["auto_pass"]))
    print("-- Answers (automatic; manual columns are filled in using GRADING_GUIDE.md) --")
    print(f"Correct (key fact in answer)     : {sum(truth(r['auto_pass']) for r in must_answer)}/{len(must_answer)}")
    print(f"Complete (both facts, conflicts) : {sum(truth(r['auto_complete']) for r in two_facts)}/{len(two_facts)}")
    print(f"Refused when it should           : {len(must_refuse) - len(wrongly_answered)}/{len(must_refuse)}"
          + (f"   answered anyway: {', '.join(wrongly_answered)}" if wrongly_answered else ""))
    print(f"Hallucination rate               : {len(wrongly_answered)}/{len(must_refuse)} unsupported questions answered")
    print(f"Overall auto pass                : {sum(truth(r['auto_pass']) for r in rows)}/{len(rows)}")
    print(f"Full answer time, median         : {_median_s(r['answer_ms'] for r in rows)}")
    for kind, results in sorted(by_type.items()):
        print(f"  {kind:12} {sum(results)}/{len(results)}")


if __name__ == "__main__":
    main()
