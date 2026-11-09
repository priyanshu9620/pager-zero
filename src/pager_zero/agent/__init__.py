"""The LangGraph agent (notes Phase 3, D-12, D-23).

triage → recall → investigate ⟲ (≤ MAX_STEPS tool calls) → diagnose → plan_fix → ⏸ approval
      → execute → verify → END

Investigation uses a read-only MCP session. `execute` opens a separate `--actions` session
only after the human resolves the interrupt. The graph is checkpointed (SQLite) so approval can
arrive from another process (`pz approve <id>`).
"""
