export interface User { user_id: string; email: string; role: 'admin' | 'employee'; department?: string | null }
export interface Category { category_id: string; category_name: string }
export interface ManagedCategory extends Category { description: string | null; document_count: number }
export interface DateRange { date_from?: string; date_to?: string }
export interface DocumentFilters { uploaded_from?: string; uploaded_to?: string; version?: number }
export interface DocumentsResult { documents: KnowledgeDocument[]; summary: Summary; scope?: 'latest_sources' | 'matching_versions' }
export interface ProfileResult {
  user: User;
  activity: { conversations: number; questions: number; responses: number; positive_feedback: number; negative_feedback: number };
  session: { expires_at: string | null; mechanism: 'opaque_cookie'; duration_hours: number };
}
export interface AnalyticsResult {
  range: { date_from: string | null; date_to: string | null; timezone: 'UTC'; generated_at: string; daily_from: string; daily_to: string };
  summary: {
    total_questions: number; questions_today: number; questions_this_week: number; total_responses: number;
    positive_feedback: number; negative_feedback: number; unrated_responses: number;
    average_response_time_ms: number | null; timed_response_count: number; unsupported_responses: number;
    no_useful_retrieval_responses: number; unknown_outcome_responses: number;
  };
  daily_activity: { date: string; questions: number; responses: number; positive_feedback: number; negative_feedback: number }[];
  frequent_questions: { question: string; count: number }[];
  document_usage: { doc_id: string; doc_name: string; version: number; category_name: string | null; responses: number }[];
  unsupported_questions: { review_id: string; conversation_id: string; user_email: string; question: string | null; answer: string; created_at: string | null; reason: string | null }[];
  definitions: string[];
}
export interface KnowledgeDocument {
  doc_id: string; doc_name: string; doc_type: string; category_id: string;
  category_name: string; version: number; status: string; is_online: boolean;
  chunk_count: number; created_at: string; source_key: string | null;
  source_hash: string | null; last_job_status: string | null; last_error: string | null;
}
export interface Summary { documents: number; online: number; offline: number; chunks: number }
export interface Chunk {
  chunk_id: string; index: number; text: string; token_count: number;
  page_number: number | null; section_heading: string | null;
  start_char: number | null; end_char: number | null;
}
export interface DocumentDetail {
  document: KnowledgeDocument; chunks: Chunk[]; versions: KnowledgeDocument[];
  pipeline: Record<string, unknown> | null; source_characters: number; warnings: string[];
}
export interface Evidence {
  rank: number; chunk_id: string; text: string; doc_id: string; doc_name: string;
  version: number; source_hash: string | null; similarity_score: number;
  page_number: number | null; section_heading: string | null;
  start_char: number | null; end_char: number | null;
}
export interface SearchResult { question: string; hits: Evidence[]; elapsed_ms: number; retrieval_only: true }
export interface AuditEvent {
  id: string; action: string; entity_type: string; entity_id: string | null;
  user_email: string; created_at: string; details: Record<string, unknown> | null;
}
export interface UploadResult { document: KnowledgeDocument; skipped: boolean; cleanup_pending: boolean }
export interface RagConfig {
  provider: string | null; model: string | null; configured: boolean; prompt_version: string;
  context_token_budget: number | null; context_byte_budget?: number; max_output_tokens: number;
  token_count_method?: string;
}
export interface Citation {
  source_id: string; doc_id: string; doc_name: string; version: number; chunk_id: string;
  page_number: number | null; section_heading: string | null; start_char: number | null;
  end_char: number | null; quote: string;
}
export interface ChatResponse {
  response_id: string; conversation_id: string; answer: string;
  status: 'answered' | 'insufficient_evidence'; citations: Citation[];
  elapsed_ms: number; model: string; prompt_version: string;
  context_tokens: number | null; context_bytes?: number; token_count_method?: string;
  grounding: { validated_quotes: number | unknown[]; rejected_claims: number | unknown[] };
}
export interface RagPreview {
  question: string; prompt_version: string; messages: { role: string; content: string }[];
  sources: (Evidence & { source_id: string })[]; context_bytes: number;
  context_tokens: null; dropped_sources: number | string[]; token_count_method: 'utf8_bytes';
}
export interface ExperimentDocument { doc_id: string; doc_name: string; source_key: string; version: number; source_hash: string }
export interface ExperimentCandidate { chunk_size: number; overlap: number; top_k: number }
export interface GoldQuestion {
  question_id: string; question: string; expected_doc_id: string | null;
  expected_source_key: string | null; evidence_quote: string | null; answerable: boolean;
}
export interface ExperimentConfig {
  documents: ExperimentDocument[]; presets: ExperimentCandidate[];
  dataset_schema: Record<string, unknown>; example: GoldQuestion[];
}
export interface ExperimentResult extends ExperimentCandidate {
  chunk_count: number; source_hit_rate: number | null; evidence_hit_rate: number | null;
  mean_reciprocal_rank: number | null; mean_latency_ms: number | null;
  answerable_count?: number; evidence_question_count?: number; unsupported_count?: number;
  questions: Record<string, unknown>[];
}
export interface ExperimentReport {
  run_id: string; status: 'completed'; corpus: ExperimentDocument[];
  results: ExperimentResult[]; warnings: string[]; selection_note: string;
}
export interface AdminUser {
  user_id: string; email: string; role: string; department: string | null;
  conversation_count: number; negative_count: number; last_activity_at: string | null;
}
export interface AdminConversation {
  conversation_id: string; user_id: string; title: string | null;
  started_at: string | null; last_activity_at: string | null; message_count: number;
}
export type ReviewFeedbackFilter = 'all' | 'up' | 'down' | 'unrated';
export type ReviewStatusFilter = 'all' | 'answered' | 'unanswered' | 'unknown';
export interface ReviewQuestion { message_id: string; content: string; created_at: string | null }
export interface ReviewAnswer extends ReviewQuestion {
  model_name: string | null; response_time_ms: number | null; top_k_used: number | null;
  insufficient_context: boolean | null; generation_metadata: Record<string, unknown> | null;
}
export interface AdminReview {
  review_id: string; conversation_id: string; conversation_title: string | null;
  user: Pick<AdminUser, 'user_id' | 'email' | 'role' | 'department'>;
  question: ReviewQuestion | null; answer: ReviewAnswer | null;
  linkage: 'explicit' | 'legacy_unlinked' | 'question_without_response';
  status: Exclude<ReviewStatusFilter, 'all'>;
  feedback: { rating: 'up' | 'down' | null; created_at: string | null; updated_at: string | null } | null;
  citations: Partial<Citation>[]; categories: string[];
}
export interface ReviewContext {
  chunk_id: string; rank: number; similarity_score: number | null; text: string;
  doc_id: string; doc_name: string; version: number; category_name: string | null;
  page_number: number | null; section_heading: string | null; start_char: number | null;
  end_char: number | null; is_online: boolean | null; status: string | null;
}
export interface PageResult { total: number; limit: number; offset: number }
export interface AdminUsersResult extends PageResult { users: AdminUser[] }
export interface AdminConversationsResult extends PageResult { conversations: AdminConversation[] }
export interface AdminReviewsResult extends PageResult { reviews: AdminReview[] }
export interface AdminReviewDetail { review: AdminReview; context: ReviewContext[] }
export interface AdminReviewFilters {
  user_id?: string; conversation_id?: string; feedback: ReviewFeedbackFilter;
  status: ReviewStatusFilter; search: string; limit: number; offset: number;
  question_from?: string; question_to?: string;
}
