CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- CORE TABLES
-- ============================================================

CREATE TABLE Users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin','employee')),
    department VARCHAR(100)
);

CREATE TABLE AuthSessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES Users(user_id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE NOT NULL
        CONSTRAINT authsessions_token_hash_length CHECK (length(token_hash) = 64),
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE DocumentCategories (
    category_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT
);

CREATE TABLE Documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_name VARCHAR(255) NOT NULL,
    doc_type VARCHAR(10) NOT NULL CHECK (doc_type IN ('pdf','docx','txt','md')),
    category_id UUID NOT NULL REFERENCES DocumentCategories(category_id),
    uploaded_by UUID REFERENCES Users(user_id),
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'staging'
        CHECK (status IN ('staging','active','deactivated','failed')),
    is_online BOOLEAN NOT NULL DEFAULT true,
    page_count INT,
    doc_path TEXT NOT NULL,
    doc_size INT,
    source_key TEXT,
    source_hash VARCHAR(64),
    text_hash VARCHAR(64),
    pipeline_fingerprint VARCHAR(64),
    pipeline_config JSONB,
    extracted_text TEXT,
    extraction_warnings JSONB,
    cleanup_pending BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE DocumentChunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES Documents(doc_id),
    chunk_text TEXT NOT NULL,
    chunk_index INT NOT NULL,
    section_heading VARCHAR(255),
    page_number INT,
    embedding_model VARCHAR(100),
    embedding_vector_id VARCHAR(100),
    token_count INT,
    start_char INT,
    end_char INT,
    chunk_created_at TIMESTAMP DEFAULT now(),
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IngestionJobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES Documents(doc_id),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending','processing','completed','failed')),
    started_at TIMESTAMP DEFAULT now(),
    completed_at TIMESTAMP,
    error_log TEXT,
    retry_count INT DEFAULT 0
);

CREATE TABLE Conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES Users(user_id),
    started_at TIMESTAMP DEFAULT now(),
    title VARCHAR(255)
);

CREATE TABLE Messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES Conversations(id),
    reply_to_message_id UUID CONSTRAINT messages_reply_to_message_id_fkey REFERENCES Messages(message_id),
    message_provider VARCHAR(10) NOT NULL CHECK (message_provider IN ('user','assistant')),
    model_name VARCHAR(100),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    response_time_ms INT,
    top_k_used INT,
    insufficient_context BOOLEAN,
    generation_metadata JSONB,
    CONSTRAINT chk_ai_fields_only_for_assistant CHECK (
        (message_provider = 'assistant')
        OR
        (message_provider = 'user' AND response_time_ms IS NULL
         AND model_name IS NULL AND top_k_used IS NULL AND insufficient_context IS NULL)
    )
);

CREATE TABLE RetrievedContext (
    message_id UUID NOT NULL REFERENCES Messages(message_id),
    chunk_id UUID NOT NULL REFERENCES DocumentChunks(chunk_id),
    similarity_score FLOAT,
    rank INT,
    PRIMARY KEY (message_id, chunk_id)
);

CREATE TABLE Feedback (
    message_id UUID NOT NULL REFERENCES Messages(message_id),
    user_id UUID NOT NULL REFERENCES Users(user_id),
    rating VARCHAR(10) CHECK (rating IN ('up','down')),
    comment TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (message_id, user_id)
);

-- ============================================================
-- EVALUATION / GOVERNANCE TABLES
-- ============================================================

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
    created_at TIMESTAMP DEFAULT now(),
    details JSONB
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_documents_uploaded_by ON Documents(uploaded_by);
CREATE INDEX idx_authsessions_user_id ON AuthSessions(user_id);
CREATE INDEX idx_authsessions_expires_at ON AuthSessions(expires_at);
CREATE INDEX idx_documents_category_id ON Documents(category_id);
CREATE UNIQUE INDEX uq_documents_active_source ON Documents(category_id, source_key)
    WHERE status = 'active' AND source_key IS NOT NULL;
CREATE UNIQUE INDEX uq_documents_source_version ON Documents(category_id, source_key, version)
    WHERE source_key IS NOT NULL;
CREATE INDEX idx_documentchunks_document_id ON DocumentChunks(document_id);
CREATE UNIQUE INDEX uq_documentchunks_document_index ON DocumentChunks(document_id, chunk_index);
CREATE INDEX idx_documentchunks_active ON DocumentChunks(is_active) WHERE is_active = true;
CREATE INDEX idx_ingestionjobs_document_id ON IngestionJobs(document_id);
CREATE INDEX idx_conversations_user_id ON Conversations(user_id);
CREATE INDEX idx_messages_conversation_id ON Messages(conversation_id);
CREATE INDEX idx_messages_reply_to_message_id ON Messages(reply_to_message_id);
CREATE INDEX idx_retrievedcontext_chunk_id ON RetrievedContext(chunk_id);
CREATE INDEX idx_feedback_user_id ON Feedback(user_id);
CREATE INDEX idx_evalquestions_expected_doc ON EvaluationQuestions(expected_doc_id);
CREATE INDEX idx_evalresults_question_id ON EvaluationResults(eval_question_id);
CREATE INDEX idx_evalresults_testrun_id ON EvaluationResults(test_run_id);
CREATE INDEX idx_auditlogs_user_id ON AuditLogs(user_id);
CREATE INDEX idx_auditlogs_entity ON AuditLogs(entity_type, entity_id);
CREATE INDEX idx_auditlogs_created_at ON AuditLogs(created_at);

-- ============================================================
-- PRIVILEGES
-- ============================================================

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO helpdesk_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO helpdesk_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO helpdesk_app;
