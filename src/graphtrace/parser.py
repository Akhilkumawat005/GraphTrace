from __future__ import annotations

from pathlib import Path
from tree_sitter import Language as TSLanguage, Parser, Node
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript

from graphtrace.models import (
    EdgeType,
    Language,
    ParseResult,
    RawReference,
    SymbolKind,
    SymbolRecord,
)

PY_LANG = TSLanguage(tspython.language())
TS_LANG = TSLanguage(tstypescript.language_typescript())
TSX_LANG = TSLanguage(tstypescript.language_tsx())


def _node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class CodeParser:
    def __init__(self) -> None:
        self._py_parser = Parser(PY_LANG)
        self._ts_parser = Parser(TS_LANG)
        self._tsx_parser = Parser(TSX_LANG)

    def parse_file(self, abs_path: Path, rel_path: str, lang: Language, is_test_file: bool) -> ParseResult:
        source_bytes = abs_path.read_bytes()
        module_symbol_id = f"{rel_path}::__module__"
        line_count = source_bytes.count(b"\n") + 1

        symbols: list[SymbolRecord] = [
            SymbolRecord(
                id=module_symbol_id,
                rel_path=rel_path,
                name=Path(rel_path).stem,
                qualified_name=rel_path,
                kind=SymbolKind.MODULE,
                start_line=1,
                end_line=line_count,
            )
        ]
        references: list[RawReference] = []

        if lang == Language.PYTHON:
            tree = self._py_parser.parse(source_bytes)
            self._walk_python(tree.root_node, source_bytes, rel_path, module_symbol_id, is_test_file, symbols, references)
        else:
            parser = self._tsx_parser if rel_path.endswith((".tsx", ".jsx")) else self._ts_parser
            tree = parser.parse(source_bytes)
            self._walk_typescript(tree.root_node, source_bytes, rel_path, module_symbol_id, is_test_file, symbols, references)

        return ParseResult(symbols=symbols, references=references)

    def _walk_python(
        self,
        node: Node,
        src: bytes,
        rel_path: str,
        enclosing_symbol_id: str,
        is_test_file: bool,
        symbols: list[SymbolRecord],
        references: list[RawReference],
        class_prefix: str = "",
    ) -> None:
        for child in node.children:
            if child.type == "class_definition":
                name_node = child.child_by_field_name("name")
                if name_node:
                    cls_name = _node_text(name_node, src)
                    qname = f"{class_prefix}.{cls_name}" if class_prefix else cls_name
                    cls_id = f"{rel_path}::{qname}"
                    symbols.append(
                        SymbolRecord(
                            id=cls_id,
                            rel_path=rel_path,
                            name=cls_name,
                            qualified_name=qname,
                            kind=SymbolKind.CLASS,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                        )
                    )
                    # Extract base classes (INHERITS)
                    superclasses = child.child_by_field_name("superclasses")
                    if superclasses:
                        for arg in superclasses.named_children:
                            base_name = _node_text(arg, src).split(".")[-1]
                            references.append(
                                RawReference(
                                    source_symbol_id=cls_id,
                                    source_rel_path=rel_path,
                                    target_name=base_name,
                                    edge_type=EdgeType.INHERITS,
                                    line_no=arg.start_point[0] + 1,
                                )
                            )
                    self._walk_python(child, src, rel_path, cls_id, is_test_file, symbols, references, qname)
                    continue

            elif child.type == "function_definition":
                name_node = child.child_by_field_name("name")
                if name_node:
                    fn_name = _node_text(name_node, src)
                    qname = f"{class_prefix}.{fn_name}" if class_prefix else fn_name
                    fn_id = f"{rel_path}::{qname}"
                    is_test = fn_name.startswith("test_") or (is_test_file and fn_name.startswith("test"))
                    kind = (
                        SymbolKind.TEST
                        if is_test
                        else (SymbolKind.METHOD if class_prefix else SymbolKind.FUNCTION)
                    )
                    params_node = child.child_by_field_name("parameters")
                    sig = f"def {fn_name}{_node_text(params_node, src)}" if params_node else f"def {fn_name}()"
                    symbols.append(
                        SymbolRecord(
                            id=fn_id,
                            rel_path=rel_path,
                            name=fn_name,
                            qualified_name=qname,
                            kind=kind,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            signature=sig,
                        )
                    )
                    self._walk_python(child, src, rel_path, fn_id, is_test_file, symbols, references, class_prefix)
                    continue

            elif child.type == "import_statement":
                for sub in child.named_children:
                    mod_name = _node_text(sub, src).split(" as ")[0].strip()
                    references.append(
                        RawReference(
                            source_symbol_id=enclosing_symbol_id,
                            source_rel_path=rel_path,
                            target_name=mod_name.split(".")[-1],
                            module_hint=mod_name,
                            edge_type=EdgeType.IMPORTS,
                            line_no=child.start_point[0] + 1,
                        )
                    )

            elif child.type == "import_from_statement":
                mod_node = child.child_by_field_name("module_name")
                mod_hint = _node_text(mod_node, src) if mod_node else None
                for sub in child.named_children:
                    if sub == mod_node:
                        continue
                    imported = _node_text(sub, src).split(" as ")[0].strip()
                    if imported and imported != "*":
                        references.append(
                            RawReference(
                                source_symbol_id=enclosing_symbol_id,
                                source_rel_path=rel_path,
                                target_name=imported,
                                module_hint=mod_hint,
                                edge_type=EdgeType.IMPORTS,
                                line_no=child.start_point[0] + 1,
                            )
                        )

            elif child.type == "call":
                fn_node = child.child_by_field_name("function")
                if fn_node:
                    raw_call = _node_text(fn_node, src)
                    callee = raw_call.split(".")[-1]
                    if callee.isidentifier():
                        references.append(
                            RawReference(
                                source_symbol_id=enclosing_symbol_id,
                                source_rel_path=rel_path,
                                target_name=callee,
                                edge_type=EdgeType.CALLS,
                                line_no=child.start_point[0] + 1,
                            )
                        )

            self._walk_python(child, src, rel_path, enclosing_symbol_id, is_test_file, symbols, references, class_prefix)

    def _walk_typescript(
        self,
        node: Node,
        src: bytes,
        rel_path: str,
        enclosing_symbol_id: str,
        is_test_file: bool,
        symbols: list[SymbolRecord],
        references: list[RawReference],
        class_prefix: str = "",
    ) -> None:
        for child in node.children:
            if child.type == "class_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    cls_name = _node_text(name_node, src)
                    cls_id = f"{rel_path}::{cls_name}"
                    symbols.append(
                        SymbolRecord(
                            id=cls_id,
                            rel_path=rel_path,
                            name=cls_name,
                            qualified_name=cls_name,
                            kind=SymbolKind.CLASS,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                        )
                    )
                    self._walk_typescript(child, src, rel_path, cls_id, is_test_file, symbols, references, cls_name)
                    continue

            elif child.type in ("function_declaration", "method_definition"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    fn_name = _node_text(name_node, src)
                    qname = f"{class_prefix}.{fn_name}" if class_prefix else fn_name
                    fn_id = f"{rel_path}::{qname}"
                    kind = SymbolKind.METHOD if class_prefix else SymbolKind.FUNCTION
                    symbols.append(
                        SymbolRecord(
                            id=fn_id,
                            rel_path=rel_path,
                            name=fn_name,
                            qualified_name=qname,
                            kind=kind,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                        )
                    )
                    self._walk_typescript(child, src, rel_path, fn_id, is_test_file, symbols, references, class_prefix)
                    continue

            elif child.type == "import_statement":
                src_node = child.child_by_field_name("source")
                mod_hint = _node_text(src_node, src).strip("\"'") if src_node else None
                if mod_hint:
                    references.append(
                        RawReference(
                            source_symbol_id=enclosing_symbol_id,
                            source_rel_path=rel_path,
                            target_name=Path(mod_hint).stem,
                            module_hint=mod_hint,
                            edge_type=EdgeType.IMPORTS,
                            line_no=child.start_point[0] + 1,
                        )
                    )

            elif child.type == "call_expression":
                fn_node = child.child_by_field_name("function")
                if fn_node:
                    raw_call = _node_text(fn_node, src)
                    callee = raw_call.split(".")[-1]
                    # Detect Jest/Vitest test blocks: test('...', ...) or it('...', ...)
                    if is_test_file and callee in ("test", "it", "describe"):
                        test_id = f"{rel_path}::{callee}_L{child.start_point[0] + 1}"
                        symbols.append(
                            SymbolRecord(
                                id=test_id,
                                rel_path=rel_path,
                                name=f"{callee}_L{child.start_point[0] + 1}",
                                qualified_name=f"{callee}_L{child.start_point[0] + 1}",
                                kind=SymbolKind.TEST,
                                start_line=child.start_point[0] + 1,
                                end_line=child.end_point[0] + 1,
                            )
                        )
                        self._walk_typescript(child, src, rel_path, test_id, is_test_file, symbols, references, class_prefix)
                        continue

                    if callee.isidentifier():
                        references.append(
                            RawReference(
                                source_symbol_id=enclosing_symbol_id,
                                source_rel_path=rel_path,
                                target_name=callee,
                                edge_type=EdgeType.CALLS,
                                line_no=child.start_point[0] + 1,
                            )
                        )

            self._walk_typescript(child, src, rel_path, enclosing_symbol_id, is_test_file, symbols, references, class_prefix)