"""The stages' own configuration, one dataclass each (the plugin's settings are
`plugin.config`):

  * `InjectionConfig` -- micro-skills: which distiller renders the block (`envfacts`, the paid
                         `distiller`, or both), its caps, and the proposer's cadence;
  * `ToolingConfig`   -- tool pruning: the evidence threshold, the optional per-task judge, and
                         Codex's capability controller.

The loop guard's benchmark configuration and the union of the three live with the benchmark,
in `experiments/te_bench/config.py`; the plugin's guard takes its settings from `plugin.config`.
"""
from dataclasses import dataclass


@dataclass
class InjectionConfig:
    """Injection (learned per-repo operating manual) stage.

    `propose_every` is the paid proposer cadence (one LLM tick per this many tasks). The
    injection Stage is the single source of truth for it and rebinds the distiller module
    global at build time. Default 5 -- first injection a few tasks earlier and more
    revision/grounding ticks over a stream.
    """
    enabled: bool = False
    propose_every: int = 5
    # Which distiller(s) render the prefix:
    #   distiller           -- the generic paid-proposer manual (`injection.Distiller`);
    #   envfacts            -- LLM-free environment facts learned from recurring evidence
    #                          shapes (`envfacts.EnvFactsDistiller`). Makes NO paid call;
    #   envfacts+distiller  -- both, the free layer first and its claims handed to the
    #                          proposer as already-stated. Paid (the proposer).
    profile: str = "distiller"
    # Caps on the environment-fact block (`envfacts`): at most this many facts, and this many
    # tokens (~4 characters each) for the whole block. 0 keeps envfacts.MAX_FACTS / MAX_TOKENS.
    max_facts: int = 0
    max_tokens: int = 0

    PROFILES = ("distiller", "envfacts", "envfacts+distiller")

    def __post_init__(self):
        if self.profile not in self.PROFILES:
            raise ValueError(f"injection profile {self.profile!r} is not one of {self.PROFILES}")

    @property
    def paid(self):
        """True when this stage will make a paid between-task LLM call."""
        return bool(self.enabled and "distiller" in self.profile)


@dataclass
class ToolingConfig:
    """Tool pruning.

    Evidence pruning (Claude Code / Copilot):
      * evidence          -- drop a tool loaded-but-unused for >= this many tasks (>0 on).
      * judge             -- add a per-task instruction-aware LLM tool judge.

    Codex capability pruning:
      * codex_untool      -- enable the evidence-gated, resolve-safe capability controller.
    """
    evidence: int = 0
    judge: bool = False
    codex_untool: bool = False

    @property
    def enabled(self):
        return bool(self.evidence or self.codex_untool)

