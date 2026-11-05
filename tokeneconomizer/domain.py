"""`DomainAdapter`: everything the mechanisms need to know about where they run.

The mechanisms are domain-neutral; what is specific to a domain -- how its logs read, which
tools are its work surface, which words name its interpreters and test runners -- enters only
through these methods. The benchmark adapters in `experiments/te_bench/adapters/` implement all
of them; the plugin's `plugin.adapter.LiveAdapter` (a real repository in a person's session)
implements only the ones its stages call: `tool_usage`, `parse_catalog_with_source`,
`protected_tools` and `env_vocabulary`.

  load_trajectory      -> every mechanism's view of a finished session
  tool_usage           -> the tools a session used (tool pruning's evidence)
  parse_catalog        -> the tools a session loaded (what pruning may propose)
  protected_tools      -> the work surface pruning never touches
  spiral_snapshot      -> a finished trajectory's loop signals, for the guard's envelope
  problem_statements   -> {task_id: task text} for the optional per-task tool judge
  env_vocabulary       -> (optional) the `microskills.observe.Vocabulary` naming this domain's
                          interpreters, runners, package tools, fetchers and probes

Running a task and grading it is not on this protocol: that is the caller's job (the plugin's
host hooks, or a benchmark harness).
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class DomainAdapter(Protocol):
    """Everything the mechanisms need to know about a task domain."""

    name: str

    # ---- reading a finished session ----
    def load_trajectory(self, path):
        """-> [(tool_name, args_dict, result_text, success_bool)] over a saved session log."""
        ...

    def tool_usage(self, path):
        """-> {tool_name: count} over a saved session log."""
        ...

    def parse_catalog(self, run_dir_path, agent):
        """-> {tool_name: description} the agent loaded for a task ({} if unavailable)."""
        ...

    def parse_catalog_with_source(self, run_dir_path, agent):
        """-> ({tool: desc}, source_name) -- OPTIONAL. source_name is None when no catalog
        source produced anything, which pruning reports as a defect rather than reading as
        "no tools loaded". The default falls back to parse_catalog with source 'unknown'."""
        return self.parse_catalog(run_dir_path, agent), "unknown"

    # ---- tool pruning ----
    def protected_tools(self):
        """-> frozenset of tools that must never be dropped (the work surface)."""
        ...

    # ---- loop guard ----
    def spiral_snapshot(self, path):
        """-> a finished trajectory's loop-signal snapshot, for the envelope tracker."""
        ...

    # ---- per-task judge inputs ----
    def problem_statements(self):
        """-> {task_id: problem statement}, for the optional per-task tool judge."""
        ...

    # ---- micro-skill vocabulary (optional) ----
    def env_vocabulary(self):
        """-> a `microskills.observe.Vocabulary` for this domain (default: Python/git)."""
        from .microskills import PYTHON_GIT
        return PYTHON_GIT
