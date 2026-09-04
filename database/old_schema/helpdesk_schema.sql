CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE Users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin','employee')),
    department VARCHAR(100)
);

CREATE TABLE Documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_name VARCHAR(255) NOT NULL,
    doc_type VARCHAR(10) NOT NULL CHECK (doc_type IN ('pdf','docx','txt','md')),
    doc_category VARCHAR(100),
    uploaded_by UUID REFERENCES Users(user_id),
    version INT DEFAULT 1,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active','deactivated')),
    page_count INT,
    doc_path TEXT NOT NULL,
    doc_size INT,
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
    message_provider VARCHAR(10) NOT NULL CHECK (message_provider IN ('user','assistant')),
    model_name VARCHAR(100),
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    response_time_ms INT,
    top_k_used INT,
    insufficient_context BOOLEAN,
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
    PRIMARY KEY (message_id, user_id)
);
CREATE INDEX idx_documents_uploaded_by ON Documents(uploaded_by);
CREATE INDEX idx_documentchunks_document_id ON DocumentChunks(document_id);
CREATE INDEX idx_documentchunks_active ON DocumentChunks(is_active) WHERE is_active = true;
CREATE INDEX idx_ingestionjobs_document_id ON IngestionJobs(document_id);
CREATE INDEX idx_conversations_user_id ON Conversations(user_id);
CREATE INDEX idx_messages_conversation_id ON Messages(conversation_id);
CREATE INDEX idx_retrievedcontext_chunk_id ON RetrievedContext(chunk_id);
CREATE INDEX idx_feedback_user_id ON Feedback(user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON Users, Documents, DocumentChunks, IngestionJobs, Conversations, Messages, RetrievedContext, Feedback TO helpdesk_app;

INSERT INTO Users (email, role, department) VALUES ('seshadri@dhti.test', 'admin', 'IT Cell');

INSERT INTO Documents (doc_name, doc_type, doc_category, uploaded_by, doc_path, doc_size)
VALUES ('Leave_Policy.pdf', 'pdf', 'HR Policy',
        (SELECT user_id FROM Users LIMIT 1), '/docs/leave_policy.pdf', 204800);

INSERT INTO DocumentChunks (document_id, chunk_text, chunk_index, page_number, embedding_model)
VALUES ((SELECT doc_id FROM Documents LIMIT 1),
        'Employees are entitled to 8 days of casual leave per calendar year.', 0, 1, 'text-embedding-3-small');

INSERT INTO IngestionJobs (document_id, status, completed_at)
VALUES ((SELECT doc_id FROM Documents LIMIT 1), 'completed', now());

INSERT INTO Conversations (user_id, title)
VALUES ((SELECT user_id FROM Users LIMIT 1), 'Leave policy question');

INSERT INTO Messages (conversation_id, message_provider, content)
VALUES ((SELECT id FROM Conversations LIMIT 1), 'user', 'How many casual leave days do I get?');

INSERT INTO Messages (conversation_id, message_provider, content, response_time_ms, model_name, top_k_used, insufficient_context)
VALUES ((SELECT id FROM Conversations LIMIT 1), 'assistant',
        'You are entitled to 8 days of casual leave per year.', 1240, 'claude-sonnet', 5, false);

INSERT INTO RetrievedContext (message_id, chunk_id, similarity_score, rank)
VALUES (
    (SELECT message_id FROM Messages WHERE message_provider = 'assistant' LIMIT 1),
    (SELECT chunk_id FROM DocumentChunks LIMIT 1),
    0.91, 1
);

INSERT INTO Feedback (message_id, user_id, rating)
VALUES (
    (SELECT message_id FROM Messages WHERE message_provider = 'assistant' LIMIT 1),
    (SELECT user_id FROM Users LIMIT 1),
    'up'
);
```
Confirm the full chain joins correctly:
```sql
SELECT u.email, c.title, m.content, m.message_provider, f.rating
FROM Users u
JOIN Conversations c ON c.user_id = u.user_id
JOIN Messages m ON m.conversation_id = c.id
LEFT JOIN Feedback f ON f.message_id = m.message_id
ORDER BY m.created_at;
```

## Step 8 — Prove the constraints reject bad data (failure path — every one of these MUST error)

```sql
-- (1) A 'user' message with response_time_ms set — must violate chk_ai_fields_only_for_assistant
INSERT INTO Messages (conversation_id, message_provider, content, response_time_ms)
VALUES ((SELECT id FROM Conversations LIMIT 1), 'user', 'test', 500);

-- (2) Invalid message_provider value — must violate the CHECK on message_provider
INSERT INTO Messages (conversation_id, message_provider, content)
VALUES ((SELECT id FROM Conversations LIMIT 1), 'bot', 'test');

-- (3) Duplicate RetrievedContext row for the same (message_id, chunk_id) — must violate composite PK
INSERT INTO RetrievedContext (message_id, chunk_id, similarity_score, rank)
VALUES (
    (SELECT message_id FROM Messages WHERE message_provider = 'assistant' LIMIT 1),
    (SELECT chunk_id FROM DocumentChunks LIMIT 1),
    0.5, 2
);

-- (4) Orphan FK — chunk_id that doesn't exist — must violate FK constraint
INSERT INTO RetrievedContext (message_id, chunk_id)
VALUES ((SELECT message_id FROM Messages LIMIT 1), gen_random_uuid());

-- (5) Invalid doc_type — must violate CHECK on Documents
INSERT INTO Documents (doc_name, doc_type, doc_path) VALUES ('test.xls', 'xls', '/x');
