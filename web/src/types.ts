export interface GraphStats {
  files: number;
  symbols: number;
  resolved_edges: number;
  co_change_edges: number;
}

export interface WorkspaceSummary {
  slug: string;
  label: string;
  stats: GraphStats;
}

export interface GraphNode {
  id: string;
  name: string;
  kind: string;
  rel_path: string;
  start_line: number;
  end_line: number;
  is_test: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  edge_type: string;
  weight: number;
}

export interface RepoGraphResponse {
  slug: string;
  stats: GraphStats;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ImpactItem {
  symbol_id: string;
  rel_path: string;
  name: string;
  kind: string;
  hop_distance: number;
  score: number;
  via_path: string[];
}

export interface TraceResponse {
  slug: string;
  query: string;
  seed_symbols: string[];
  total_impacted: number;
  impacted_symbols: ImpactItem[];
  impacted_tests: ImpactItem[];
  suggested_test_command: string | null;
}

export interface CodeSlice {
  symbol_id: string;
  rel_path: string;
  kind: string;
  start_line: number;
  end_line: number;
  relation: string;
  code: string;
}

export interface AskResponse {
  question: string;
  model: string;
  seed_symbols: string[];
  cited_slices: CodeSlice[];
  answer: string;
}