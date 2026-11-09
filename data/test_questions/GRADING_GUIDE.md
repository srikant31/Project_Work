# Grading guide for evaluation results

Use this guide to fill the manual columns of each `results/eval_*.csv` file.
Write `1` (yes), `0` (no) or leave blank where the rule says "not applicable".
Grade from the `question`, `answer` and the source document only — never from
memory of what the policy "should" say.

The automatic columns are a first pass, not the final grade:

| Automatic column | What it checks |
|---|---|
| `auto_pass` | Answerable: the key fact appears in the answer. Unsupported: the app refused. Misleading: it refused or quoted the true fact. |
| `auto_complete` | The answer contains every key fact (both sides of a conflict where `key_fact_2` exists). |
| `fact_found`, `fact2_found` | Each key fact, ignoring table pipes, bold marks, `<br>` and spacing. |

## Manual columns

### `manual_correct` — is the answer right?
- `1` — the answer states the fact in the question's key fact (or an equally
  correct statement from the expected document).
- `0` — the answer is wrong, answers a different question, or refuses although
  the documents contain the answer.
- Unsupported questions (`should_answer = no`): `1` if the app refused, `0` if it answered.
- Misleading questions: `1` if it refused or quoted the true fact; `0` if it
  went along with the false premise.

### `manual_relevant` — does the answer address the question asked?
- `1` — every quote helps answer this question.
- `0` — at least one quote is about something else (padding, a different
  policy, a different employee type).
- Blank when the app refused.

### `manual_grounded` — is every claim supported by the cited source?
The app guarantees each quote is copied exactly. This column checks meaning:
- `1` — each quote, read in its source, supports the claim it is used for
  (right document, right section, right version, not a superseded rule).
- `0` — a quote is exact but misleading in context, for example a row read
  under the wrong table header, or an outdated rule that another document replaces.
- Blank when the app refused.

### `manual_complete` — is anything important missing?
- `1` — the answer gives everything the question needs, including conditions,
  exceptions and, for conflict questions (C02–C04), **both** conflicting
  statements or a clear statement of which document is authoritative.
- `0` — part of the answer is missing (for example only "5 years" for C02, or
  a limit without the approval it needs).
- Blank when the app refused.

### `manual_notes`
One short line for every `0`: what went wrong and where (retrieval, prompt,
model, quote validation, document conflict). These notes become the failure
analysis in the evaluation report.

## Reporting the results

| Report metric | Calculation |
|---|---|
| Answer correctness | `manual_correct` = 1 / questions with `should_answer = yes` |
| Answer relevance | `manual_relevant` = 1 / answered questions |
| Groundedness | `manual_grounded` = 1 / answered questions |
| Completeness | `manual_complete` = 1 / answered questions |
| Hallucination rate | unsupported questions answered / unsupported questions |
| Retrieval time and full answer time | medians from the SUMMARY block |

State the configuration with every number: retrieval mode, Top-K, chunk
size/overlap, prompt version, model.
