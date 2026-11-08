import type { AdminConversationsResult, AdminReviewDetail, AdminReviewFilters, AdminReviewsResult, AdminUsersResult, AuditEvent, Category, ChatResponse, DocumentDetail, ExperimentCandidate, ExperimentConfig, ExperimentReport, GoldQuestion, KnowledgeDocument, RagConfig, RagPreview, SearchResult, UploadResult, User } from './types';
import type { AnalyticsResult, DateRange, DocumentFilters, DocumentsResult, ManagedCategory, ProfileResult } from './types';
import { analyticsQuery, documentQuery } from './adminData.ts';
import type { ConversationHistory, ConversationsResult } from './types';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export interface RequestOptions extends RequestInit { timeoutMs?: number }

export function requestTimeoutMs(path: string): number {
  const endpoint = path.split('?')[0];
  if (endpoint === '/documents/upload' || /^\/documents\/[^/]+\/(replace|reindex)$/.test(endpoint)) return 900_000;
  if (endpoint === '/experiments/run') return 600_000;
  if (endpoint === '/chat') return 180_000;
  if (endpoint === '/search' || endpoint === '/rag/preview') return 120_000;
  return 30_000;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = requestTimeoutMs(path), signal: callerSignal, ...fetchOptions } = options;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiError('Invalid request timeout.', 0);
  const headers = new Headers(options.headers);
  headers.set('X-Requested-With', 'helpdesk-ui');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const controller = new AbortController();
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel: () => void = () => {};
  const stopped = new Promise<never>((_, reject) => {
    cancel = () => { reject(new ApiError('Request cancelled.', 0)); controller.abort(); };
    if (callerSignal?.aborted) { cancel(); return; }
    callerSignal?.addEventListener('abort', cancel, { once: true });
    timer = setTimeout(() => {
      timedOut = true;
      const method = (fetchOptions.method || 'GET').toUpperCase();
      const mutating = !['GET', 'HEAD', 'OPTIONS'].includes(method);
      reject(new ApiError(`The request timed out after ${Math.ceil(timeoutMs / 1000)} seconds. ${mutating ? 'The server may still finish processing. Refresh the relevant history or document status before submitting again.' : 'Please try again.'}`, 408));
      controller.abort();
    }, timeoutMs);
  });
  try {
    if (callerSignal?.aborted) return await stopped;
    const operation = async (): Promise<T> => {
      const response = await fetch(`/api${path}`, { ...fetchOptions, headers, credentials: 'same-origin', signal: controller.signal });
      if (controller.signal.aborted) return await stopped;
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (controller.signal.aborted) return await stopped;
        const detail = body?.detail;
        const message = typeof detail === 'string' ? detail
          : Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg || 'Invalid input').join('; ')
          : `Request could not be completed (${response.status}).`;
        if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') window.dispatchEvent(new Event('helpdesk:unauthorized'));
        throw new ApiError(message, response.status);
      }
      if (response.status === 204) return undefined as T;
      return await response.json() as T;
    };
    // Cover both response headers and body parsing, including a stalled body stream.
    return await Promise.race([operation(), stopped]);
  } catch (caught) {
    if (caught instanceof ApiError) throw caught;
    if (timedOut) throw new ApiError('The request timed out. Check the operation status before submitting again.', 408);
    if (callerSignal?.aborted) throw new ApiError('Request cancelled.', 0);
    throw new ApiError('Cannot reach the helpdesk server. Check that the local backend is running.', 0);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    callerSignal?.removeEventListener('abort', cancel);
  }
}

export const api = {
  me: () => request<{ user: User }>('/auth/me'),
  login: (email: string, password: string) => request<{ user: User }>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<unknown>('/auth/logout', { method: 'POST' }),
  documents: (filters: DocumentFilters = {}) => { const query = documentQuery(filters); return request<DocumentsResult>(`/documents${query ? `?${query}` : ''}`); },
  categories: () => request<{ categories: Category[] }>('/documents/categories'),
  managedCategories: () => request<{ categories: ManagedCategory[] }>('/categories'),
  createCategory: (category_name: string, description: string | null) => request<{ category: ManagedCategory }>('/categories', { method: 'POST', body: JSON.stringify({ category_name, description }) }),
  updateCategory: (id: string, category_name: string, description: string | null) => request<{ category: ManagedCategory }>(`/categories/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ category_name, description }) }),
  deleteCategory: (id: string) => request<void>(`/categories/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  analytics: (range: DateRange = {}) => { const query = analyticsQuery(range); return request<AnalyticsResult>(`/analytics${query ? `?${query}` : ''}`); },
  profile: () => request<ProfileResult>('/profile'),
  conversations: (limit = 20, offset = 0) => request<ConversationsResult>(`/conversations?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`),
  conversation: (id: string) => request<ConversationHistory>(`/conversations/${encodeURIComponent(id)}`),
  document: (id: string) => request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`),
  upload: (body: FormData) => request<UploadResult>('/documents/upload', { method: 'POST', body }),
  replace: (id: string, body: FormData) => request<UploadResult>(`/documents/${encodeURIComponent(id)}/replace`, { method: 'POST', body }),
  reindex: (id: string, chunk_size: number, overlap: number) => request<UploadResult>(`/documents/${encodeURIComponent(id)}/reindex`, { method: 'POST', body: JSON.stringify({ chunk_size, overlap }) }),
  availability: (id: string, is_online: boolean) => request<{ document: KnowledgeDocument }>(`/documents/${encodeURIComponent(id)}/availability`, { method: 'PATCH', body: JSON.stringify({ is_online }) }),
  search: (question: string, top_k: number, mode?: string) => request<SearchResult>(`/search${mode ? `?mode=${encodeURIComponent(mode)}` : ''}`, { method: 'POST', body: JSON.stringify({ question, top_k }) }),
  audit: () => request<{ events: AuditEvent[] }>('/audit-logs'),
  evidence: (responseId: string, chunkId: string) => request<{ text: string; quotes: string[]; doc_name: string; version: number; page_number: number | null; section_heading: string | null; chunk_id: string }>(`/responses/${encodeURIComponent(responseId)}/evidence/${encodeURIComponent(chunkId)}`),
  ragConfig: () => request<RagConfig>('/rag/config'),
  ragPreview: (question: string, top_k: number) => request<RagPreview>('/rag/preview', { method: 'POST', body: JSON.stringify({ question, top_k }) }),
  chat: (question: string, top_k: number, conversation_id?: string) => request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify({ question, top_k, ...(conversation_id ? { conversation_id } : {}) }) }),
  feedback: (response_id: string, rating: 'up' | 'down', comment?: string) => request<{ response_id: string; rating: 'up' | 'down'; comment: string | null }>('/feedback', { method: 'POST', body: JSON.stringify({ response_id, rating, ...(comment ? { comment } : {}) }) }),
  experimentConfig: () => request<ExperimentConfig>('/experiments/config'),
  runExperiment: (document_ids: string[], questions: GoldQuestion[], configurations: ExperimentCandidate[]) => request<ExperimentReport>('/experiments/run', { method: 'POST', body: JSON.stringify({ document_ids, questions, configurations }) }),
  adminUsers: (search: string, limit: number, offset: number) => request<AdminUsersResult>(`/admin/users?${new URLSearchParams({ search, limit: String(limit), offset: String(offset) })}`),
  adminConversations: (user_id: string, limit: number, offset: number) => request<AdminConversationsResult>(`/admin/conversations?${new URLSearchParams({ user_id, limit: String(limit), offset: String(offset) })}`),
  adminReviews: (filters: AdminReviewFilters) => {
    const query = new URLSearchParams({ feedback: filters.feedback, status: filters.status, search: filters.search, limit: String(filters.limit), offset: String(filters.offset) });
    if (filters.user_id) query.set('user_id', filters.user_id);
    if (filters.conversation_id) query.set('conversation_id', filters.conversation_id);
    if (filters.question_from) query.set('question_from', filters.question_from);
    if (filters.question_to) query.set('question_to', filters.question_to);
    return request<AdminReviewsResult>(`/admin/reviews?${query}`);
  },
  adminReview: (message_id: string) => request<AdminReviewDetail>(`/admin/reviews/${encodeURIComponent(message_id)}`),
};

export function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }
