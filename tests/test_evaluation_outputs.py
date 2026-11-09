from pathlib import Path

from scripts.run_eval import evaluation_filename


def test_default_evaluation_filename_preserves_existing_resume_path():
    assert evaluation_filename("hybrid", 5, "grounded-quotes-current-chat-v2", "openai/gpt-oss-120b") == (
        "eval_hybrid_k5_grounded-quotes-current-chat-v2_openai-gpt-oss-120b.csv")


def test_run_labels_separate_csv_and_chunk_resume_files():
    first = Path(evaluation_filename("hybrid", 8, "prompt", "model", "topk8"))
    second = Path(evaluation_filename("hybrid", 8, "prompt", "model", "diag"))
    assert first != second
    assert first.stem + "_chunks.jsonl" == "eval_hybrid_k8_prompt_model_topk8_chunks.jsonl"


def test_run_label_cannot_escape_results_directory():
    name = evaluation_filename("hybrid", 5, "prompt", "model", "../../outside/label")
    assert Path(name).name == name and "/" not in name and "\\" not in name
