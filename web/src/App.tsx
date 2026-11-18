import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  Check,
  Copy,
  FileCode2,
  GitBranch,
  Layers,
  Loader2,
  Play,
  Search,
  Sparkles,
  Terminal,
} from 'lucide-react';
import { askSubgraph, fetchRepoGraph, fetchWorkspaces, indexGithubRepo, traceSymbol } from './api';
import { DependencyCanvas } from './components/DependencyCanvas';
import type { AskResponse, GraphNode, RepoGraphResponse, TraceResponse, WorkspaceSummary } from './types';

export function App() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [activeSlug, setActiveSlug] = useState<string>('demo');
  const [repoUrlInput, setRepoUrlInput] = useState<string>('');
  const [indexing, setIndexing] = useState<boolean>(false);

  const [graphData, setGraphData] = useState<RepoGraphResponse | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [symbolSearch, setSymbolSearch] = useState<string>('compute_blast_radius');
  const [traceData, setTraceData] = useState<TraceResponse | null>(null);
  const [tracing, setTracing] = useState<boolean>(false);

  const [rightTab, setRightTab] = useState<'impact' | 'ask'>('impact');
  const [question, setQuestion] = useState<string>('What breaks if I change the return format of compute_blast_radius?');
  const [askData, setAskData] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState<boolean>(false);

  const [copiedCmd, setCopiedCmd] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadWorkspaceGraph = async (slug: string, defaultSymbol?: string) => {
    setErrorMsg(null);
    try {
      const g = await fetchRepoGraph(slug);
      setGraphData(g);
      const targetSym = defaultSymbol || (g.nodes[0]?.name ?? '');
      if (targetSym) {
        setSymbolSearch(targetSym);
        const tr = await traceSymbol(slug, targetSym);
        setTraceData(tr);
        const matchedNode = g.nodes.find((n) => tr.seed_symbols.includes(n.id)) || null;
        setSelectedNode(matchedNode);
      } else {
        setTraceData(null);
        setSelectedNode(null);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to load repository graph.');
    }
  };

  useEffect(() => {
    fetchWorkspaces()
      .then((list) => setWorkspaces(list))
      .catch(() => {});
    loadWorkspaceGraph('demo', 'compute_blast_radius');
  }, []);

  const handleIndexRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoUrlInput.trim()) return;
    setIndexing(true);
    setErrorMsg(null);
    try {
      const res = await indexGithubRepo(repoUrlInput.trim());
      const updated = await fetchWorkspaces();
      setWorkspaces(updated);
      setActiveSlug(res.slug);
      setRepoUrlInput('');
      await loadWorkspaceGraph(res.slug);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Repository indexing failed.');
    } finally {
      setIndexing(false);
    }
  };

  const handleRunTrace = async (querySymbol: string, explicitNode?: GraphNode) => {
    if (!querySymbol.trim()) return;
    setTracing(true);
    setErrorMsg(null);
    try {
      const tr = await traceSymbol(activeSlug, querySymbol.trim());
      setTraceData(tr);
      if (explicitNode) {
        setSelectedNode(explicitNode);
      } else if (graphData) {
        const found = graphData.nodes.find((n) => tr.seed_symbols.includes(n.id)) || null;
        setSelectedNode(found);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Symbol trace failed.');
    } finally {
      setTracing(false);
    }
  };

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setErrorMsg(null);
    try {
      const res = await askSubgraph(activeSlug, question.trim(), selectedNode?.name || symbolSearch);
      setAskData(res);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Subgraph synthesis failed.');
    } finally {
      setAsking(false);
    }
  };

  const impactedMap = useMemo(() => {
    const map = new Map<string, number>();
    if (traceData) {
      for (const item of traceData.impacted_symbols) {
        map.set(item.symbol_id, item.hop_distance);
      }
    }
    return map;
  }, [traceData]);

  const copyTestCommand = () => {
    if (!traceData?.suggested_test_command) return;
    navigator.clipboard.writeText(traceData.suggested_test_command);
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 1800);
  };

  return (
    <div className="min-h-screen flex flex-col bg-[#090d16] text-slate-100">
      {/* Header Bar */}
      <header className="border-b border-slate-800 bg-slate-950/80 backdrop-blur px-6 py-3.5 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-400">
            <GitBranch className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight flex items-center gap-2">
              GraphTrace <span className="text-xs font-mono px-2 py-0.5 rounded bg-sky-500/15 text-sky-300">CLOUD</span>
            </h1>
            <p className="text-xs text-slate-400">Structural &amp; Evolutionary Codebase Impact Engine</p>
          </div>
        </div>

        {/* Workspace Switcher + GitHub Repo Indexer */}
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={activeSlug}
            onChange={(e) => {
              const slug = e.target.value;
              setActiveSlug(slug);
              loadWorkspaceGraph(slug, slug === 'demo' ? 'compute_blast_radius' : undefined);
            }}
            className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-sky-500"
          >
            {workspaces.length === 0 && <option value="demo">GraphTrace (Built-in Self-Analysis)</option>}
            {workspaces.map((ws) => (
              <option key={ws.slug} value={ws.slug}>
                {ws.label} ({ws.stats.symbols} symbols)
              </option>
            ))}
          </select>

          <form onSubmit={handleIndexRepo} className="flex items-center gap-2">
            <input
              type="url"
              placeholder="https://github.com/owner/repo"
              value={repoUrlInput}
              onChange={(e) => setRepoUrlInput(e.target.value)}
              className="w-64 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-sky-500"
            />
            <button
              type="submit"
              disabled={indexing}
              className="flex items-center gap-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm font-medium px-3.5 py-1.5 rounded-lg transition cursor-pointer"
            >
              {indexing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {indexing ? 'Indexing...' : 'Index Repo'}
            </button>
          </form>
        </div>
      </header>

      {/* Error Banner */}
      {errorMsg && (
        <div className="mx-6 mt-4 px-4 py-2.5 rounded-lg bg-red-950/70 border border-red-800 text-red-200 text-sm flex items-center justify-between">
          <span>{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} className="text-xs underline ml-4 cursor-pointer">
            Dismiss
          </button>
        </div>
      )}

      {/* Metrics Ribbon */}
      {graphData && (
        <div className="px-6 pt-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-slate-900/70 border border-slate-800 rounded-xl px-4 py-2.5 flex items-center justify-between">
            <span className="text-xs text-slate-400 flex items-center gap-1.5">
              <FileCode2 className="w-4 h-4 text-sky-400" /> Indexed Files
            </span>
            <span className="text-base font-bold font-mono">{graphData.stats.files}</span>
          </div>
          <div className="bg-slate-900/70 border border-slate-800 rounded-xl px-4 py-2.5 flex items-center justify-between">
            <span className="text-xs text-slate-400 flex items-center gap-1.5">
              <Layers className="w-4 h-4 text-indigo-400" /> AST Symbols
            </span>
            <span className="text-base font-bold font-mono">{graphData.stats.symbols}</span>
          </div>
          <div className="bg-slate-900/70 border border-slate-800 rounded-xl px-4 py-2.5 flex items-center justify-between">
            <span className="text-xs text-slate-400 flex items-center gap-1.5">
              <Activity className="w-4 h-4 text-emerald-400" /> Structural Edges
            </span>
            <span className="text-base font-bold font-mono">{graphData.stats.resolved_edges}</span>
          </div>
          <div className="bg-slate-900/70 border border-slate-800 rounded-xl px-4 py-2.5 flex items-center justify-between">
            <span className="text-xs text-slate-400 flex items-center gap-1.5">
              <GitBranch className="w-4 h-4 text-amber-400" /> Git Co-Changes
            </span>
            <span className="text-base font-bold font-mono">{graphData.stats.co_change_edges}</span>
          </div>
        </div>
      )}

      {/* Main Split Workspace */}
      <main className="flex-1 p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Interactive Dependency Graph */}
        <section className="lg:col-span-7 flex flex-col gap-3 min-h-[540px]">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleRunTrace(symbolSearch);
            }}
            className="flex items-center gap-2"
          >
            <div className="relative flex-1">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={symbolSearch}
                onChange={(e) => setSymbolSearch(e.target.value)}
                placeholder="Trace symbol blast radius (e.g. SymbolResolver, compute_blast_radius)..."
                className="w-full bg-slate-900 border border-slate-800 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-sky-500"
              />
            </div>
            <button
              type="submit"
              disabled={tracing}
              className="bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition cursor-pointer"
            >
              {tracing ? 'Tracing...' : 'Trace Impact'}
            </button>
          </form>

          <div className="flex-1 min-h-[480px]">
            {graphData ? (
              <DependencyCanvas
                nodes={graphData.nodes}
                edges={graphData.edges}
                selectedSymbolId={selectedNode?.id ?? traceData?.seed_symbols[0] ?? null}
                impactedMap={impactedMap}
                onSelectNode={(node) => {
                  setSymbolSearch(node.name);
                  handleRunTrace(node.id, node);
                }}
              />
            ) : (
              <div className="w-full h-full rounded-xl border border-slate-800 bg-slate-900/40 flex items-center justify-center text-slate-400 text-sm">
                Loading dependency graph...
              </div>
            )}
          </div>
        </section>

        {/* Right: Blast Radius & Grounded Gemini Architect */}
        <section className="lg:col-span-5 bg-slate-900/60 border border-slate-800 rounded-xl flex flex-col overflow-hidden">
          <div className="flex border-b border-slate-800">
            <button
              onClick={() => setRightTab('impact')}
              className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 border-b-2 transition cursor-pointer ${
                rightTab === 'impact'
                  ? 'border-amber-500 text-amber-400 bg-slate-900'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Activity className="w-4 h-4" /> Blast Radius ({traceData?.total_impacted ?? 0})
            </button>
            <button
              onClick={() => setRightTab('ask')}
              className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 border-b-2 transition cursor-pointer ${
                rightTab === 'ask'
                  ? 'border-sky-500 text-sky-400 bg-slate-900'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              <Bot className="w-4 h-4" /> AI Subgraph Architect
            </button>
          </div>

          {rightTab === 'impact' ? (
            <div className="p-4 flex-1 overflow-y-auto space-y-4">
              {traceData ? (
                <>
                  <div className="bg-slate-950 border border-slate-800 rounded-lg p-3">
                    <div className="text-xs text-slate-400 mb-1">Active Seed Symbol</div>
                    <div className="text-xs font-mono text-amber-400 break-all">
                      {traceData.seed_symbols.join(', ')}
                    </div>
                  </div>

                  {traceData.suggested_test_command && (
                    <div className="bg-emerald-950/40 border border-emerald-800/70 rounded-lg p-3">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-semibold text-emerald-300 flex items-center gap-1.5">
                          <Terminal className="w-3.5 h-3.5" /> Minimal Selective Test Command (
                          {traceData.impacted_tests.length} tests)
                        </span>
                        <button
                          onClick={copyTestCommand}
                          className="text-xs text-emerald-300 hover:text-white flex items-center gap-1 cursor-pointer"
                        >
                          {copiedCmd ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                          {copiedCmd ? 'Copied' : 'Copy'}
                        </button>
                      </div>
                      <code className="block text-xs font-mono text-emerald-200 bg-slate-950/80 px-2.5 py-1.5 rounded overflow-x-auto">
                        {traceData.suggested_test_command}
                      </code>
                    </div>
                  )}

                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                      Transitive Downstream Dependents
                    </h3>
                    <div className="space-y-2 max-h-[340px] overflow-y-auto pr-1">
                      {traceData.impacted_symbols.map((item) => (
                        <div
                          key={item.symbol_id}
                          className="bg-slate-950/80 border border-slate-800 rounded-lg p-2.5 text-xs"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono font-semibold text-slate-100 truncate">{item.name}</span>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                                Hop {item.hop_distance}
                              </span>
                              <span className="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 font-mono">
                                {item.score.toFixed(1)}
                              </span>
                            </div>
                          </div>
                          <div className="text-[11px] text-slate-400 font-mono mt-1 truncate">{item.rel_path}</div>
                          <div className="text-[10px] text-slate-500 font-mono mt-1 truncate">
                            {item.via_path.map((p) => p.split('::').pop()).join(' → ')}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-sm text-slate-400 py-12 text-center">
                  Click any node on the graph or search a symbol to inspect its transitive blast radius.
                </div>
              )}
            </div>
          ) : (
            <div className="p-4 flex-1 flex flex-col gap-3 overflow-y-auto">
              <form onSubmit={handleAsk} className="space-y-2">
                <textarea
                  rows={2}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="Ask an architectural question grounded in this dependency subgraph..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-sm text-slate-100 focus:outline-none focus:border-sky-500"
                />
                <button
                  type="submit"
                  disabled={asking}
                  className="w-full flex items-center justify-center gap-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm font-medium py-2 rounded-lg transition cursor-pointer"
                >
                  {asking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  {asking ? 'Synthesizing Subgraph Answer...' : 'Ask Grounded Gemini Architect'}
                </button>
              </form>

              {askData && (
                <div className="space-y-3 flex-1 overflow-y-auto">
                  <div className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs leading-relaxed text-slate-200 whitespace-pre-wrap">
                    <div className="text-[11px] font-mono text-sky-400 mb-2">
                      Model: {askData.model} • {askData.cited_slices.length} verified slices
                    </div>
                    {askData.answer}
                  </div>

                  <div>
                    <div className="text-xs font-semibold text-slate-400 mb-1.5">
                      Verified Source Slices ({askData.cited_slices.length})
                    </div>
                    <div className="space-y-2 max-h-48 overflow-y-auto">
                      {askData.cited_slices.map((sl) => (
                        <div key={sl.symbol_id} className="bg-slate-950 border border-slate-800 rounded p-2">
                          <div className="text-[11px] font-mono text-amber-300">
                            {sl.rel_path}:{sl.start_line}-{sl.end_line} ({sl.relation})
                          </div>
                          <pre className="text-[10px] font-mono text-slate-400 mt-1 overflow-x-auto max-h-24">
                            {sl.code}
                          </pre>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;