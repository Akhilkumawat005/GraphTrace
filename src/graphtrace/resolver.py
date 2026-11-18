from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path, PurePosixPath

from graphtrace.models import EdgeType, ResolvedEdge
from graphtrace.storage import GraphStorage

EDGE_BASE_WEIGHTS: dict[EdgeType, float] = {
    EdgeType.CALLS: 1.0,
    EdgeType.INHERITS: 0.85,
    EdgeType.IMPORTS: 0.60,
    EdgeType.CO_CHANGES: 0.50,
}

# Common noisy identifiers that should not match across unrelated files heuristically
GENERIC_NAMES = {
    "get",
    "set",
    "add",
    "update",
    "delete",
    "remove",
    "create",
    "init",
    "run",
    "start",
    "stop",
    "close",
    "save",
    "load",
    "read",
    "write",
    "parse",
    "format",
    "items",
    "keys",
    "values",
    "append",
    "extend",
    "pop",
    "clear",
    "copy",
    "split",
    "join",
    "strip",
    "replace",
    "encode",
    "decode",
    "print",
    "len",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "tuple",
    "type",
    "super",
    "isinstance",
    "hasattr",
    "getattr",
    "setattr",
}


class SymbolResolver:
    """Resolves raw AST references into cross-file symbol dependency edges."""

    def __init__(self, repo_root: Path, storage: GraphStorage) -> None:
        self.repo_root = repo_root.resolve()
        self.storage = storage
        self.ts_paths = self._load_tsconfig_paths()

    def _load_tsconfig_paths(self) -> dict[str, list[str]]:
        tsconfig = self.repo_root / "tsconfig.json"
        if not tsconfig.is_file():
            return {}
        try:
            raw = tsconfig.read_text(encoding="utf-8")
            # Strip simple single-line comments before JSON parse
            cleaned = "\n".join(
                line for line in raw.splitlines() if not line.strip().startswith("//")
            )
            data = json.loads(cleaned)
            compiler_opts = data.get("compilerOptions", {})
            return compiler_opts.get("paths", {})
        except Exception:
            return {}

    def _build_module_lookup(self, rel_paths: set[str]) -> dict[str, str]:
        """Map normalized module identifiers (e.g. 'graphtrace.parser', 'src/auth') -> rel_path."""
        lookup: dict[str, str] = {}
        for rp in rel_paths:
            p = PurePosixPath(rp)
            no_ext = p.with_suffix("").as_posix()
            lookup[no_ext] = rp
            lookup[rp] = rp

            # Strip leading 'src/' if present
            if no_ext.startswith("src/"):
                stripped = no_ext[4:]
                lookup[stripped] = rp
                lookup[stripped.replace("/", ".")] = rp

            # Dotted Python module name
            dotted = no_ext.replace("/", ".")
            lookup[dotted] = rp
            if dotted.endswith(".__init__"):
                pkg = dotted[: -len(".__init__")]
                lookup[pkg] = rp
                lookup[pkg.replace(".", "/")] = rp

            # Index file in TS/JS (e.g. 'components/Button/index.ts' -> 'components/Button')
            if p.stem == "index":
                parent_dir = p.parent.as_posix()
                if parent_dir != ".":
                    lookup[parent_dir] = rp
                    if parent_dir.startswith("src/"):
                        lookup[parent_dir[4:]] = rp

        return lookup

    def _resolve_module_path(
        self, source_rel_path: str, module_hint: str | None, module_lookup: dict[str, str]
    ) -> str | None:
        if not module_hint:
            return None

        # 1. Handle relative TS/JS imports ('./auth', '../utils/hash')
        if module_hint.startswith(".") and "/" in module_hint:
            base_dir = PurePosixPath(source_rel_path).parent
            candidate = (base_dir / module_hint).as_posix()
            # Normalize '..' segments manually on PurePosixPath
            parts: list[str] = []
            for part in candidate.split("/"):
                if part in ("", "."):
                    continue
                if part == ".." and parts:
                    parts.pop()
                else:
                    parts.append(part)
            norm = "/".join(parts)
            if norm in module_lookup:
                return module_lookup[norm]

        # 2. Handle Python relative imports ('.parser', '..models')
        if module_hint.startswith(".") and "/" not in module_hint:
            dots = len(module_hint) - len(module_hint.lstrip("."))
            remainder = module_hint[dots:].replace(".", "/")
            base = PurePosixPath(source_rel_path).parent
            for _ in range(dots - 1):
                base = base.parent
            cand = (base / remainder).as_posix() if remainder else base.as_posix()
            if cand in module_lookup:
                return module_lookup[cand]

        # 3. Handle TypeScript tsconfig path aliases (e.g. '@/utils/auth' -> 'src/utils/auth')
        for alias_pattern, targets in self.ts_paths.items():
            prefix = alias_pattern.rstrip("*")
            if module_hint.startswith(prefix) and targets:
                suffix = module_hint[len(prefix) :]
                target_prefix = targets[0].rstrip("*").lstrip("./")
                mapped = f"{target_prefix}{suffix}"
                if mapped in module_lookup:
                    return module_lookup[mapped]

        # 4. Direct dotted or slash lookup
        if module_hint in module_lookup:
            return module_lookup[module_hint]
        dotted_clean = module_hint.replace(".", "/")
        if dotted_clean in module_lookup:
            return module_lookup[dotted_clean]

        return None

    def resolve_all(self) -> list[ResolvedEdge]:
        files = self.storage.fetch_all_files()
        symbols = self.storage.fetch_all_symbols()
        raw_refs = self.storage.fetch_all_raw_references()

        rel_paths = {row["rel_path"] for row in files}
        module_lookup = self._build_module_lookup(rel_paths)

        # Indexes for fast lookup
        symbols_by_file: dict[str, list[dict]] = defaultdict(list)
        symbols_by_name: dict[str, list[dict]] = defaultdict(list)
        for s in symbols:
            s_dict = dict(s)
            symbols_by_file[s_dict["rel_path"]].append(s_dict)
            if s_dict["kind"] != "module":
                symbols_by_name[s_dict["name"]].append(s_dict)

        # Track explicit file-level imports: source_rel_path -> {imported_name: target_file_rel_path}
        file_import_map: dict[str, dict[str, str]] = defaultdict(dict)
        file_imported_modules: dict[str, set[str]] = defaultdict(set)

        resolved_edges: list[ResolvedEdge] = []

        # Pass 1: Resolve IMPORTS edges and populate per-file symbol import table
        for ref in raw_refs:
            if ref["edge_type"] != EdgeType.IMPORTS.value:
                continue
            src_path = ref["source_rel_path"]
            target_file = self._resolve_module_path(src_path, ref["module_hint"], module_lookup)
            if not target_file or target_file == src_path:
                continue

            file_imported_modules[src_path].add(target_file)
            target_name = ref["target_name"]
            file_import_map[src_path][target_name] = target_file

            # Link to specific exported symbol if defined in target_file, else module node
            matched_syms = [
                s for s in symbols_by_file[target_file] if s["name"] == target_name
            ]
            target_id = matched_syms[0]["id"] if matched_syms else f"{target_file}::__module__"

            if ref["source_symbol_id"] != target_id:
                resolved_edges.append(
                    ResolvedEdge(
                        source_id=ref["source_symbol_id"],
                        target_id=target_id,
                        edge_type=EdgeType.IMPORTS,
                        weight=EDGE_BASE_WEIGHTS[EdgeType.IMPORTS],
                        confidence=1.0,
                        line_no=ref["line_no"],
                    )
                )

        # Pass 2: Resolve CALLS and INHERITS edges using tiered scope resolution
        for ref in raw_refs:
            etype_str = ref["edge_type"]
            if etype_str == EdgeType.IMPORTS.value:
                continue

            etype = EdgeType(etype_str)
            src_sym_id = ref["source_symbol_id"]
            src_path = ref["source_rel_path"]
            target_name = ref["target_name"]
            line_no = ref["line_no"]

            target_id: str | None = None
            confidence = 0.0

            # Tier 1: Imported symbol from an explicitly imported local file
            imported_file = file_import_map.get(src_path, {}).get(target_name)
            if imported_file:
                candidates = [
                    s for s in symbols_by_file[imported_file] if s["name"] == target_name
                ]
                if candidates:
                    target_id = candidates[0]["id"]
                    confidence = 1.0

            # Tier 2: Defined within the same file
            if not target_id:
                local_candidates = [
                    s
                    for s in symbols_by_file[src_path]
                    if s["name"] == target_name and s["id"] != src_sym_id
                ]
                if local_candidates:
                    target_id = local_candidates[0]["id"]
                    confidence = 0.90

            # Tier 3: Symbol defined in one of the files imported by src_path
            if not target_id:
                for mod_file in file_imported_modules.get(src_path, set()):
                    mod_candidates = [
                        s for s in symbols_by_file[mod_file] if s["name"] == target_name
                    ]
                    if mod_candidates:
                        target_id = mod_candidates[0]["id"]
                        confidence = 0.85
                        break

            # Tier 4: Project-wide non-generic symbol match (catches method calls on passed objects)
            if not target_id and target_name not in GENERIC_NAMES and len(target_name) > 2:
                global_candidates = [
                    s
                    for s in symbols_by_name.get(target_name, [])
                    if s["id"] != src_sym_id and s["kind"] != "test"
                ]
                if 1 <= len(global_candidates) <= 3:
                    for cand in global_candidates:
                        resolved_edges.append(
                            ResolvedEdge(
                                source_id=src_sym_id,
                                target_id=cand["id"],
                                edge_type=etype,
                                weight=EDGE_BASE_WEIGHTS[etype],
                                confidence=round(0.65 / len(global_candidates), 3),
                                line_no=line_no,
                            )
                        )
                    continue

            if target_id and target_id != src_sym_id:
                resolved_edges.append(
                    ResolvedEdge(
                        source_id=src_sym_id,
                        target_id=target_id,
                        edge_type=etype,
                        weight=EDGE_BASE_WEIGHTS[etype],
                        confidence=confidence,
                        line_no=line_no,
                    )
                )

        return resolved_edges