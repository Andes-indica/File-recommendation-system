export type Source = {
  id: string;
  name: string;
  root: string;
  kind: string;
  paused: number;
  file_count: number;
  last_sync?: string;
  error?: string;
  job_status?: string;
};
export type Job = {
  id: string;
  source_id: string | null;
  kind: string;
  status: string;
  total: number;
  completed: number;
  indexed: number;
  skipped: number;
  removed: number;
  error?: string;
  details: { file: string; reason: string }[];
};
export type FileItem = {
  id: number;
  name: string;
  path: string;
  extension: string;
  size: number;
  modified_at: string;
  source_id: string;
  source_name: string;
  explanation?: string;
  signals?: string[];
  excerpt?: string;
  citation?: string;
  recommendation_id?: number;
  warnings?: string[];
  score?: number;
};
export type Filters = {
  source_id?: string | null;
  extension?: string | null;
  after?: string | null;
  before?: string | null;
};
export type Settings = {
  personalization: boolean;
  history: boolean;
  working_source: string | null;
  embedding_model: string;
  embedding_status: string;
  embedding_error: string;
  reranker_model: string;
  reranker_status: string;
  reranker_error: string;
  llm_provider: string;
  llm_model: string;
  cloud_url: string;
  cloud_model: string;
  cloud_key_set: boolean;
};
export type Health = {
  status: string;
  file_count: number;
  pending_jobs: number;
  embeddings: string;
  llm_provider: string;
  formats: string[];
};
export type SearchResult = {
  request_id: string;
  session_id: string;
  query: string;
  effective_query: string;
  filters: Filters;
  results: FileItem[];
  confidence: number;
  status: string;
  message: string;
  diagnostics: {
    strategy: string;
    planned_strategy: string;
    reason: string;
    candidate_count: number;
    expanded: boolean;
    reranked: boolean;
    model_used: boolean;
    fallback: string | null;
    latency_ms: number;
  };
};
export type Progress = { stage: string; message: string; elapsed_ms: number };
export type Turn = {
  id: string;
  query: string;
  status: string;
  result: SearchResult | null;
  events: Progress[];
  filters: Filters;
};
export type Session = {
  id: string;
  title: string;
  created_at: string;
  turns?: Turn[];
};
export type Preview = {
  id: number;
  name: string;
  path: string;
  sections: { label: string; text: string }[];
  warnings: string[];
  available: boolean;
};
export type Models = {
  ollama: string;
  models: string[];
  suggested_model: string;
  embedding_model: string;
  embedding_status: string;
  embedding_error: string;
  semantic_installed: boolean;
};
