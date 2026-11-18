import type { AskResponse, RepoGraphResponse, TraceResponse, WorkspaceSummary } from './types';

// Uses VITE_API_BASE_URL in production (Vercel -> Render) and falls back to '' (Vite proxy) locally
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || data.error || detail;
    } catch {
      // ignore non-JSON body
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchWorkspaces(): Promise<WorkspaceSummary[]> {
  const res = await fetch(`${API_BASE}/api/repos`);
  const data = await handleJson<{ workspaces: WorkspaceSummary[] }>(res);
  return data.workspaces;
}

export async function indexGithubRepo(repoUrl: string, force = false): Promise<{ slug: string }> {
  const res = await fetch(`${API_BASE}/api/repos/index`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_url: repoUrl, force }),
  });
  return handleJson<{ slug: string }>(res);
}

export async function fetchRepoGraph(slug: string, maxNodes = 140): Promise<RepoGraphResponse> {
  const res = await fetch(`${API_BASE}/api/repos/${encodeURIComponent(slug)}/graph?max_nodes=${maxNodes}`);
  return handleJson<RepoGraphResponse>(res);
}

export async function traceSymbol(slug: string, symbol: string, depth = 5): Promise<TraceResponse> {
  const res = await fetch(
    `${API_BASE}/api/repos/${encodeURIComponent(slug)}/trace?symbol=${encodeURIComponent(symbol)}&depth=${depth}`
  );
  return handleJson<TraceResponse>(res);
}

export async function askSubgraph(slug: string, question: string, symbol?: string): Promise<AskResponse> {
  const res = await fetch(`${API_BASE}/api/repos/${encodeURIComponent(slug)}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, symbol: symbol || null }),
  });
  return handleJson<AskResponse>(res);
}