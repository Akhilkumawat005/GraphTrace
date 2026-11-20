---
name: graphtrace
description: Deterministic codebase dependency, blast-radius, and selective test impact analysis via GraphTrace MCP. Use BEFORE refactoring functions/classes, modifying shared interfaces, reviewing pull requests, or selecting tests after code edits in Python or TypeScript/JavaScript repositories.
---

# GraphTrace Codebase Impact & Blast-Radius Skill

Use the GraphTrace MCP server to inspect structural (Tree-sitter AST) and evolutionary (Git co-change) dependencies before making or validating code changes.

## When to Invoke GraphTrace

Always invoke GraphTrace tools when:
1. **Modifying or Refactoring a Symbol:** Before changing a function signature, return type, class method, or shared utility (`get_blast_radius`).
2. **Validating Changes / Selecting Tests:** After editing files or before committing, to find the minimal affected test suite instead of running the entire repository (`get_impacted_tests`).
3. **Investigating Coupling:** When assessing risk on core modules that may have hidden evolutionary co-change dependencies.

## Available MCP Tools

### 1. `get_blast_radius`
Traces transitive downstream dependents of a target symbol using weighted reverse-BFS over AST (`CALLS`, `INHERITS`, `IMPORTS`) and Git `CO_CHANGES` edges.
- **Inputs:**
  - `symbol` (string, required): Function, method, or class name to trace (e.g., `SymbolResolver`, `compute_blast_radius`).
  - `max_hops` (integer, optional, default `3`): Maximum dependency traversal depth.
- **How to use the output:**
  - Review all impacted callers and file paths with confidence scores before editing.
  - Warn the user if high-confidence downstream callers or public API endpoints will break.

### 2. `get_impacted_tests`
Maps uncommitted `git diff` hunks or changed files to AST symbol boundaries and returns the minimal impacted test files and exact test command.
- **How to use the output:**
  - Run the suggested `pytest` or `vitest` command directly to verify changes quickly without full-suite overhead.

## Recommended Workflow for Agents

1. **Before editing:** Call `get_blast_radius` on the target symbol(s) you plan to change.
2. **Inspect downstream impact:** Check every affected file/line returned and plan updates for breaking callers.
3. **Apply edits** across the symbol and its impacted callers.
4. **After editing:** Call `get_impacted_tests` and execute the minimal test command to confirm zero regressions.