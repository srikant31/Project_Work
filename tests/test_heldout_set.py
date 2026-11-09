import csv
import hashlib
from pathlib import Path


def test_heldout_set_is_frozen_and_has_requested_structure():
    root = Path(__file__).resolve().parents[1]
    path = root / "data/test_questions/heldout_set.csv"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "62608661a30dc44de50c3e50a03ec41ed74ec9d969a2160c4fb4d4e0dceb518e"
    with path.open(encoding="utf-8", newline="") as handle:
        records = list(csv.reader(handle))
    assert len(records) == 13
    assert all(len(row) == len(records[0]) for row in records[1:])
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert len({row["id"] for row in rows}) == 12
    assert sum(row["type"] in {"direct", "paraphrase"} for row in rows) == 6
    assert sum(row["type"] == "table" for row in rows) == 2
    assert sum(row["type"] == "conflict" for row in rows) == 1
    assert sum(row["should_answer"] == "no" for row in rows) == 3
    baseline = list(csv.DictReader((root / "data/test_questions/eval_set.csv").open(encoding="utf-8", newline="")))
    assert not {r["question"] for r in rows} & {r["question"] for r in baseline}
