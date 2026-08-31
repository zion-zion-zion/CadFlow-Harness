import type { ProductSemanticModel } from './product-state';

export type ProjectState = 'Draft' | 'Running' | 'Succeeded' | 'Failed' | 'Stopped';
export type AgentHarness = 'deepagents';
export type TokenUsage = {
  input_tokens: number | null;
  cached_input_tokens: number | null;
  uncached_input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
};
export type LivePreviewState = 'waiting' | 'stale' | 'building' | 'current' | 'failed' | 'paused';
export type LivePreviewStatus = {
  state: LivePreviewState;
  revision: number;
  source_hash: string | null;
  updated_at: string | null;
  error: string | null;
  stdout: string | null;
  stderr: string | null;
  artifact_available: boolean;
};
export type Project = {
  project_id: string;
  name: string;
  state: ProjectState;
  created_at: string;
  updated_at: string;
  prompt: string | null;
  failure_reason: string | null;
  harness: AgentHarness;
  scene_available: boolean;
  product_available: boolean;
  result_kind: 'part' | 'assembly' | null;
  product_status: 'Accepted' | null;
  artifact_version: number | null;
  turn_count: number;
  diagnostics_available: boolean;
  duration_seconds: number | null;
  token_usage: TokenUsage | null;
  preview: LivePreviewStatus;
};
export type ConversationTurn = {
  turn_id: string;
  sequence: number;
  request_id: string | null;
  retry_of: string | null;
  user_message: string;
  assistant_message: string;
  status: 'running' | 'succeeded' | 'failed' | 'stopped' | 'cancelled';
  created_at: string;
  completed_at: string | null;
  artifact_version: number | null;
  error: string | null;
};
export type ConversationResponse = {
  conversation_id: string;
  turns: ConversationTurn[];
  current_artifact_version: number | null;
};
export type MessageResponse = {
  turn: ConversationTurn;
  project: Project;
  artifact: { version: number | null; scene_available: boolean };
  duplicate: boolean;
};
export type ProgressRecord = {
  id: number;
  created_at: string;
  stage: string;
  tool: string | null;
  attempt: number | null;
  result: string | null;
  preview?: { attempt: number; revision: number; operation: string };
};
export type ProductFile = { path: string; sha256: string; size_bytes: number; download_url: string };
export type ProductPart = {
  part_id: string;
  quantity: number;
  component_paths: string[];
  step_path: string;
  sha256: string;
  size_bytes: number;
  download_url: string;
};
export type ProductBomItem = {
  part_id: string;
  name: string | null;
  material: string | null;
  quantity: number;
  component_paths: string[];
  step_path: string;
};
export type ProductValidationCheck = {
  check_id: string;
  status: 'passed' | 'failed' | 'not_applicable' | string;
  message?: string;
  evidence?: unknown;
};
export type ProductValidationReport = {
  status: string;
  checks: ProductValidationCheck[];
  blocking_failures: string[];
};
export type ProductResponse = {
  schema_version: 'cadflow-product-api/v1';
  result_kind: 'part' | 'assembly';
  status: 'Accepted';
  manifest_url: string;
  summary: {
    component_count: number;
    leaf_part_count: number;
    unique_part_count: number;
    solid_count: number;
    volume_mm3: number;
  };
  files: Record<string, ProductFile>;
  parts: ProductPart[];
  semantic_model: ProductSemanticModel | null;
  bom: ProductBomItem[];
  assumptions: string[];
  validation_report: ProductValidationReport | null;
};

export function parseApiError(payload: unknown, status: number): string {
  return typeof payload === 'object' && payload !== null && 'detail' in payload
    ? String((payload as { detail: unknown }).detail)
    : `Request failed (${status})`;
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try { payload = JSON.parse(text) as unknown; }
    catch { payload = text; }
  }
  if (!response.ok) throw new Error(parseApiError(payload, response.status));
  return payload as T;
}

export async function requestBinary(url: string, init?: RequestInit, failureMessage?: string | ((status: number) => string)): Promise<Blob> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const text = await response.text();
    let payload: unknown = text;
    try { payload = text ? JSON.parse(text) as unknown : null; } catch { /* preserve text */ }
    throw new Error(typeof failureMessage === 'function' ? failureMessage(response.status) : failureMessage ?? parseApiError(payload, response.status));
  }
  return response.blob();
}

export async function requestArrayBuffer(url: string, init?: RequestInit, failureMessage?: string | ((status: number) => string)): Promise<ArrayBuffer> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const text = await response.text();
    let payload: unknown = text;
    try { payload = text ? JSON.parse(text) as unknown : null; } catch { /* preserve text */ }
    throw new Error(typeof failureMessage === 'function' ? failureMessage(response.status) : failureMessage ?? parseApiError(payload, response.status));
  }
  return response.arrayBuffer();
}
