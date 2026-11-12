"""External tool scaffolds — the baselines the mechanisms are compared against.

These are NOT mechanisms. A mechanism is TokenEconomizer's own controller acting through
the harness's existing seams; a scaffold here is somebody else's off-the-shelf tool
(`rtk`, Serena, aider's repo map) installed into the sealed agent's container so its cost
and resolve can be measured on the same honest basis.

They sit with the experiments, beside the mechanisms they are the comparison for, and two
invariants are kept structurally rather than by convention:

  * **Nothing here reaches a container.** Every container interaction arrives as a callable
    on `ScaffoldContext` (`exec_`, `copy_in`, `read_bytes`, `host_run`), supplied by
    whichever harness is driving. No module in this package imports Docker, or subprocess-
    invokes it, or imports `te_harness` -- so any harness that can implement those four can
    run these baselines, and `tests/test_baselines.py` asserts the import surface.
  * **The core never imports this package.** It lives with the experiments, outside
    `tokeneconomizer`, so the economizer -- and the plugin built on it -- cannot see a
    baseline even by accident. The one non-stdlib import the repo-map arm needs is in the
    core's `repomap_gen.py`, a subprocess entry point that nothing imports.

One scaffold answers two questions, and nothing else:

  * `setup(ctx)`           -- install/configure itself in the started container, returning
                              a notes dict that lands in `row.json` (`arm_setup`, versions,
                              whether each step actually took effect)
  * `patch_excludes`       -- paths it writes under /testbed that must NEVER appear in the
                              graded diff

The `patch_excludes` contract is the one that silently corrupts a result if you get it
wrong. A scaffold that writes project instructions into `/testbed/CLAUDE.md` puts that file
in `git diff`, so the graded patch would carry the scaffold's own prompt into the official
evaluation -- scoring the scaffold's text rather than the agent's fix. The runner subtracts
these paths before grading, and records what it subtracted.

What a scaffold may NOT do:

  * change the cost basis. Setup runs before the agent starts and its tokens are the
    agent's own; the scaffold's install traffic is not billed to the model because it is
    not model traffic. An LLM-calling scaffold would have to charge itself through
    `tokeneconomizer.ledger` like a mechanism does, and none of these do.
  * exclude a native tool. Serena wants the agent to stop using Read/Grep/Glob, and the
    original study achieved that with an INSTRUCTION, not `--disallowed-tools`. That is
    kept deliberately: Read is in PROTECTED_TOOLS, so an exclusion would be filtered out
    and the arm would silently run as native. An instruction that the agent may disobey is
    a real property of the baseline, and measuring the baseline as it actually behaves is
    the point.

On the word "scaffold": the directory is `baselines/` because that is the category --
external tool baselines, as opposed to mechanisms. The per-item noun stays "scaffold"
because `baseline` is already the name of the NAIVE arm, and a row carrying both
`arm: "baseline"` and `baseline: "rtk"` would be unreadable.

Registry: `get(name)` resolves a scaffold. Which arm carries which scaffold is the arm's own
`scaffold` field (`te_bench.arms.SCAFFOLD_ARMS` collects them); the harness resolves that name
here.
"""
from .base import Scaffold, ScaffoldContext, AgentProfile, ScaffoldError
from .repomap import RepoMapScaffold
from .rtk import RtkScaffold
from .serena import SerenaScaffold

__all__ = ["Scaffold", "ScaffoldContext", "AgentProfile", "ScaffoldError",
           "SCAFFOLDS", "get"]

SCAFFOLDS = {
    "rtk": RtkScaffold,
    "serena": SerenaScaffold,
    "repomap": RepoMapScaffold,
}


def get(name):
    """The scaffold instance for `name`, or None for a falsy name (= no scaffold)."""
    if not name:
        return None
    try:
        return SCAFFOLDS[name]()
    except KeyError:
        raise ScaffoldError(
            f"unknown scaffold {name!r}; known: {', '.join(sorted(SCAFFOLDS))}") from None
