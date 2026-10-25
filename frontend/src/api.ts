import type { AdminConversationsResult, AdminReviewDetail, AdminReviewFilters, AdminReviewsResult, AdminUsersResult, AuditEvent, Category, ChatResponse, DocumentDetail, ExperimentCandidate, ExperimentConfig, ExperimentReport, GoldQuestion, KnowledgeDocument, RagConfig, RagPreview, SearchResult, UploadResult, User } from './types';
import type { AnalyticsResult, DateRange, DocumentFilters, DocumentsResult, ManagedCategory, ProfileResult } from './types';
import { analyticsQuery, documentQuery } from './adminData.ts';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('X-Requested-With', 'helpdesk-ui');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' });
  } catch {
    throw new ApiError('Cannot reach the helpdesk server. Check that the local backend is running.', 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg || 'Invalid input').join('; ')
      : `Request could not be completed (${response.status}).`;
    // Initial session probes can overlap in React StrictMode. A delayed probe
    // must never clear a session that has just signed in successfully.
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') window.dispatchEvent(new Event('helpdesk:unauthorized'));
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
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
  document: (id: string) => request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`),
  upload: (body: FormData) => request<UploadResult>('/documents/upload', { method: 'POST', body }),
  replace: (id: string, body: FormData) => request<UploadResult>(`/documents/${encodeURIComponent(id)}/replace`, { method: 'POST', body }),
  reindex: (id: string, chunk_size: number, overlap: number) => request<UploadResult>(`/documents/${encodeURIComponent(id)}/reindex`, { method: 'POST', body: JSON.stringify({ chunk_size, overlap }) }),
  availability: (id: string, is_online: boolean) => request<{ document: KnowledgeDocument }>(`/documents/${encodeURIComponent(id)}/availability`, { method: 'PATCH', body: JSON.stringify({ is_online }) }),
  search: (question: string, top_k: number) => request<SearchResult>('/search', { method: 'POST', body: JSON.stringify({ question, top_k }) }),
  audit: () => request<{ events: AuditEvent[] }>('/audit-logs'),
  ragConfig: () => request<RagConfig>('/rag/config'),
  ragPreview: (question: string, top_k: number) => request<RagPreview>('/rag/preview', { method: 'POST', body: JSON.stringify({ question, top_k }) }),
  chat: (question: string, top_k: number, conversation_id?: string) => request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify({ question, top_k, ...(conversation_id ? { conversation_id } : {}) }) }),
  feedback: (response_id: string, rating: 'up' | 'down') => request<{ response_id: string; rating: 'up' | 'down' }>('/feedback', { method: 'POST', body: JSON.stringify({ response_id, rating }) }),
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
