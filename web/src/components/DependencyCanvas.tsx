import React, { useMemo } from 'react';
import type { GraphEdge, GraphNode } from '../types';

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedSymbolId: string | null;
  impactedMap: Map<string, number>; // symbol_id -> hop_distance
  onSelectNode: (node: GraphNode) => void;
}

export const DependencyCanvas: React.FC<Props> = ({
  nodes,
  edges,
  selectedSymbolId,
  impactedMap,
  onSelectNode,
}) => {
  const { positionedNodes, width, height } = useMemo(() => {
    const w = 960;
    const h = 600;
    const cx = w / 2;
    const cy = h / 2;

    // Group nodes by file path so symbols in the same module cluster naturally
    const byFile = new Map<string, GraphNode[]>();
    for (const n of nodes) {
      const list = byFile.get(n.rel_path) || [];
      list.push(n);
      byFile.set(n.rel_path, list);
    }

    const fileGroups = Array.from(byFile.entries());
    const totalFiles = Math.max(1, fileGroups.length);
    const coords = new Map<string, { x: number; y: number }>();

    fileGroups.forEach(([, group], fIdx) => {
      const fileAngle = (2 * Math.PI * fIdx) / totalFiles - Math.PI / 2;
      const ringRadius = totalFiles <= 4 ? 145 : 215;
      const gx = cx + ringRadius * Math.cos(fileAngle);
      const gy = cy + ringRadius * Math.sin(fileAngle);

      group.forEach((node, nIdx) => {
        if (group.length === 1) {
          coords.set(node.id, { x: gx, y: gy });
        } else {
          const subAngle = fileAngle + (2 * Math.PI * nIdx) / group.length;
          const subRadius = Math.min(68, 22 + group.length * 4);
          coords.set(node.id, {
            x: Math.max(40, Math.min(w - 40, gx + subRadius * Math.cos(subAngle))),
            y: Math.max(40, Math.min(h - 40, gy + subRadius * Math.sin(subAngle))),
          });
        }
      });
    });

    return { positionedNodes: coords, width: w, height: h };
  }, [nodes]);

  const getNodeStyle = (node: GraphNode) => {
    if (node.id === selectedSymbolId) {
      return { fill: '#f59e0b', stroke: '#fef3c7', r: 9 };
    }
    const hop = impactedMap.get(node.id);
    if (hop === 1) return { fill: '#ef4444', stroke: '#fecaca', r: 7.5 };
    if (hop === 2) return { fill: '#f97316', stroke: '#fed7aa', r: 6.5 };
    if (hop && hop >= 3) return { fill: '#eab308', stroke: '#fef08a', r: 6 };
    if (node.is_test) return { fill: '#10b981', stroke: '#064e3b', r: 5 };
    return { fill: '#38bdf8', stroke: '#0c4a6e', r: 5.5 };
  };

  return (
    <div className="relative w-full h-full bg-[#060911] rounded-xl border border-slate-800 overflow-hidden">
      <div className="absolute top-3 left-3 z-10 flex flex-wrap gap-3 bg-slate-900/90 backdrop-blur px-3 py-1.5 rounded-lg border border-slate-800 text-xs text-slate-300">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block" /> Seed
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block" /> Hop 1
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-orange-500 inline-block" /> Hop 2
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-yellow-500 inline-block" /> Hop 3+
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block" /> Test
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-sky-400 inline-block" /> Symbol
        </span>
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full select-none">
        <defs>
          <marker id="arrow-muted" markerWidth="6" markerHeight="6" refX="12" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill="#334155" />
          </marker>
          <marker id="arrow-hot" markerWidth="7" markerHeight="7" refX="13" refY="3.5" orient="auto">
            <path d="M0,0 L7,3.5 L0,7 Z" fill="#f87171" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const p1 = positionedNodes.get(e.source);
          const p2 = positionedNodes.get(e.target);
          if (!p1 || !p2) return null;

          const isImpactedEdge =
            (e.source === selectedSymbolId || impactedMap.has(e.source)) &&
            (e.target === selectedSymbolId || impactedMap.has(e.target));

          return (
            <line
              key={i}
              x1={p1.x}
              y1={p1.y}
              x2={p2.x}
              y2={p2.y}
              stroke={isImpactedEdge ? '#f87171' : '#1e293b'}
              strokeWidth={isImpactedEdge ? 1.8 : 0.9}
              strokeDasharray={e.edge_type === 'CO_CHANGES' ? '4 3' : undefined}
              markerEnd={isImpactedEdge ? 'url(#arrow-hot)' : 'url(#arrow-muted)'}
              opacity={isImpactedEdge ? 0.95 : 0.55}
            />
          );
        })}

        {nodes.map((node) => {
          const pos = positionedNodes.get(node.id);
          if (!pos) return null;
          const style = getNodeStyle(node);
          const isHighlighted = node.id === selectedSymbolId || impactedMap.has(node.id);

          return (
            <g
              key={node.id}
              transform={`translate(${pos.x}, ${pos.y})`}
              onClick={() => onSelectNode(node)}
              className="cursor-pointer group"
            >
              <circle
                r={style.r}
                fill={style.fill}
                stroke={style.stroke}
                strokeWidth={1.8}
                className="transition-transform duration-150 group-hover:scale-125"
              />
              {(isHighlighted || nodes.length <= 45) && (
                <text
                  y={-style.r - 4}
                  textAnchor="middle"
                  className={`text-[10px] font-mono pointer-events-none ${
                    isHighlighted ? 'fill-slate-100 font-semibold' : 'fill-slate-400'
                  }`}
                >
                  {node.name}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
};