from __future__ import annotations

import time
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JAVASCRIPT = "javascript"


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TEST = "test"


class EdgeType(str, Enum):
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    INHERITS = "INHERITS"
    CO_CHANGES = "CO_CHANGES"


class FileRecord(BaseModel):
    rel_path: str
    sha256: str
    language: str
    is_test: bool = False
    line_count: int = 0
    mtime: float = 0.0
    indexed_at: float = Field(default_factory=time.time)

    @model_validator(mode="before")
    @classmethod
    def _sync_timestamps(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if "indexed_at" not in data and "mtime" in data:
                data["indexed_at"] = data["mtime"]
            elif "mtime" not in data and "indexed_at" in data:
                data["mtime"] = data["indexed_at"]
        return data


class SymbolRecord(BaseModel):
    id: str
    rel_path: str
    name: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    signature: str = ""


class RawReference(BaseModel):
    source_symbol_id: str
    source_file: str = ""
    target_name: str
    module_hint: str | None = None
    edge_type: EdgeType
    line_no: int = 0
    line_number: int = 0

    @model_validator(mode="before")
    @classmethod
    def _sync_fields(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if "line_number" not in data and "line_no" in data:
                data["line_number"] = data["line_no"]
            elif "line_no" not in data and "line_number" in data:
                data["line_no"] = data["line_number"]
            if not data.get("source_file") and "source_symbol_id" in data:
                data["source_file"] = str(data["source_symbol_id"]).split("::", 1)[0]
        return data


class ParseResult(BaseModel):
    symbols: list[SymbolRecord] = Field(default_factory=list)
    references: list[RawReference] = Field(default_factory=list)


class ResolvedEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence: float = Field(ge=0.0, le=1.0)


class CoChangeEdge(BaseModel):
    file_a: str
    file_b: str
    co_change_count: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ImpactItem(BaseModel):
    symbol_id: str
    rel_path: str
    name: str
    kind: str
    hop_distance: int
    score: float
    via_path: list[str]


class DiffImpactReport(BaseModel):
    diff_target: str
    modified_files: list[str]
    mutated_symbols: list[str]
    impacted_symbols: list[ImpactItem]
    impacted_tests: list[ImpactItem]
    suggested_test_command: str | None = None


class CodeSlice(BaseModel):
    symbol_id: str
    rel_path: str
    kind: str
    start_line: int
    end_line: int
    relation: str
    code: str


class AskResponse(BaseModel):
    question: str
    model: str
    seed_symbols: list[str]
    cited_slices: list[CodeSlice]
    answer: str