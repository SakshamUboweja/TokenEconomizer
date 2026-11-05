"""The benchmark's view of the mechanisms' configuration.

`InjectionConfig` and `ToolingConfig` live in `tokeneconomizer.config`, because the plugin
builds them too. The loop guard as a benchmark harness runs it (`SpiralConfig`), the union of
the three (`EconomizerConfig`) and the paid-call predicate (`needs_optimizer`) are used only
here, so they stay out of the shipped package.
"""
from dataclasses import dataclass, field

from tokeneconomizer.config import InjectionConfig, ToolingConfig

__all__ = ["InjectionConfig", "ToolingConfig", "SpiralConfig", "EconomizerConfig",
           "needs_optimizer"]


@dataclass
class SpiralConfig:
    """The loop guard in a benchmark harness (generic for Claude/Copilot; Codex-native for
    Codex). The plugin's guard takes its settings from `plugin.config` instead."""
    guard: bool = False            # generic loop guard (Claude / Copilot)
    judge: bool = False            # sampled LLM trace-judge for the abort decision
    nudge: bool = False            # rung-1 same-container nudge before abort
    nudge_llm: bool = False        # LLM-written nudge redirect (vs the count template)
    frozen_envelope: float = None  # pre-registered resolved-turn envelope for SHORT
                                   # streams where within-stream p90 never warms up;
                                   # None = learn within-stream only.
    codex_spiral: bool = False     # Codex-native controller (post-green finalize)
    # Which Codex policy: "post-green" (the shipped finalize rule alone) or "defensive"
    # (finalize plus every deterministic spiral signature, two distinct nudges per task,
    # never an abort).
    codex_spiral_profile: str = "post-green"
    CODEX_SPIRAL_PROFILES = ("post-green", "defensive")

    def __post_init__(self):
        if self.codex_spiral_profile not in self.CODEX_SPIRAL_PROFILES:
            raise ValueError(f"codex_spiral_profile must be one of "
                             f"{self.CODEX_SPIRAL_PROFILES}, got {self.codex_spiral_profile!r}")

    @property
    def enabled(self):
        return bool(self.guard or self.codex_spiral)


def needs_optimizer(config):
    """Whether ANY enabled mechanism will make a paid between-task LLM call.

    The three clauses mirror `te_bench.facade.Economizer.__init__` exactly -- it resolves an
    optimizer runner for injection when `injection.paid` (enabled AND a profile that
    carries the paid `distiller`; the LLM-free `envfacts` profile never calls), for pruning when
    `tooling.judge`, and for spiral when the guard is on AND it has a judge or an LLM
    nudge writer. Kept as one predicate because callers OUTSIDE the facade need the
    aggregate answer: a harness whose mechanism transport must be preflighted should not
    pay for (or depend on) that probe when the arm will never call it. Arm `mode` is NOT
    the test -- `spiral-only` and `untool-only` are `standalone` and do make paid calls.

    experiments/tests/test_optimizer_basis.py asserts this agrees with the facade for every
    arm in the registry, so the two cannot drift.
    """
    return bool(
        config.injection.paid
        or config.tooling.judge
        or (config.spiral.guard and (config.spiral.judge or config.spiral.nudge_llm))
    )


@dataclass
class EconomizerConfig:
    """The three mechanisms' configs for one arm; unnamed ones default off."""
    injection: InjectionConfig = field(default_factory=InjectionConfig)
    tooling: ToolingConfig = field(default_factory=ToolingConfig)
    spiral: SpiralConfig = field(default_factory=SpiralConfig)
