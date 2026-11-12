"""Failure-loop intervention: notice a prompt-turn stuck in a failing loop, redirect it once,
then stop it (headless) or tell the person (interactive).

  * `state`       -- `SpiralState`, the per-call fold of a trajectory's loop signals;
  * `rules`       -- thresholds, the free gate, the strict heuristic, `assess_abort`, the redirect;
  * `envelope`    -- the per-repository length of turns that ended well, and its storage;
  * `shell_edits` -- edits made through the shell, counted like edit-tool edits;
  * `turn`        -- `TurnGuard`, the guard for one prompt-turn, one tool call at a time;
  * `judge`       -- the optional paid stuck judge and redirect writer.

Stdlib only; never imports the plugin runtime.
"""
from .envelope import (EnvelopeTracker, base_thresholds, load_envelope, record_turn,
                       thresholds)
from .judge import llm_nudge_writer, llm_stuck_judge
from .rules import DEFAULT_THRESHOLDS, gate_for_judge, scaled_thresholds
from .shell_edits import shell_edit_targets
from .state import SpiralState, state_from_trajectory
from .turn import VERIFY_RE, TurnGuard

__all__ = ["EnvelopeTracker", "base_thresholds", "load_envelope", "record_turn", "thresholds",
           "llm_nudge_writer", "llm_stuck_judge", "DEFAULT_THRESHOLDS", "gate_for_judge",
           "scaled_thresholds", "shell_edit_targets", "SpiralState", "state_from_trajectory",
           "VERIFY_RE", "TurnGuard"]
