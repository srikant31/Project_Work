CREATE TABLE EvaluationQuestions (
    eval_question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text TEXT NOT NULL,
    expected_answer_text TEXT,
    expected_doc_id UUID REFERENCES Documents(doc_id),
    expected_page_number INT,
    category VARCHAR(100),
    difficulty VARCHAR(10) CHECK (difficulty IN ('easy','medium','hard')),
    created_at TIMESTAMP DEFAULT now()
);
CREATE TABLE EvaluationTestRuns (
    test_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_name VARCHAR(255),
    chunking_config JSONB,
    top_k_config INT,
    embedding_model_config VARCHAR(100),
    llm_config JSONB,
    run_at TIMESTAMP DEFAULT now()
);

CREATE TABLE EvaluationResults (
    eval_result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_question_id UUID NOT NULL REFERENCES EvaluationQuestions(eval_question_id),
    test_run_id UUID NOT NULL REFERENCES EvaluationTestRuns(test_run_id),
    retrieved_chunks JSONB,
    generated_answer TEXT,
    retrieval_hit BOOLEAN,
    grounded BOOLEAN,
    hallucination_detected BOOLEAN,
    answer_correct BOOLEAN,
    retrieval_precision FLOAT,
    retrieval_recall FLOAT,
    response_time_ms INT,
    evaluated_at TIMESTAMP DEFAULT now()
);
CREATE TABLE AuditLogs (
    audit_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES Users(user_id),
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX idx_evalquestions_expected_doc ON EvaluationQuestions(expected_doc_id);
CREATE INDEX idx_evalresults_question_id ON EvaluationResults(eval_question_id);
CREATE INDEX idx_evalresults_testrun_id ON EvaluationResults(test_run_id);
CREATE INDEX idx_auditlogs_user_id ON AuditLogs(user_id);
CREATE INDEX idx_auditlogs_entity ON AuditLogs(entity_type, entity_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON EvaluationQuestions, EvaluationTestRuns, EvaluationResults, AuditLogs TO helpdesk_app;

