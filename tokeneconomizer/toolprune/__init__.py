"""Tool pruning: stop paying, on every request, for tool definitions a repository never uses.

  * `stage` -- `ToolingStage`: tracks which loaded tools go unused, session after session, and
               proposes dropping a tool once it has gone unused for `evidence` sessions (never a
               protected tool, never one ever used);
  * `codex` -- `CodexUntoolProfile`: the same idea for Codex, whose unit is a capability
               package switched off in its config rather than a single tool;
  * `tools` -- the protected work surface and the optional per-task tool judge.

Stdlib only; never imports the plugin runtime.
"""
from .stage import ToolingStage

__all__ = ["ToolingStage"]
