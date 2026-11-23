# SPDX-License-Identifier: Apache-2.0
"""SunCore planner (Phase 4 WP8, ADR-0003): 48 h in 15-minute steps, HiGHS LP + heuristic.

Entry point for the runtime (WP9): build a :class:`~suncore.planner.inputs.PlannerInputs`
with :func:`~suncore.planner.inputs.build_inputs` from the services' outputs, then call
:func:`~suncore.planner.run.plan` (or :func:`~suncore.planner.run.run` for the details) in a
worker thread. Depends on ``suncore.core`` only (plus ``highspy``/NumPy in the model); no I/O.
"""

from __future__ import annotations

from suncore.planner.inputs import PlannerInputs, PlannerWeights, build_inputs
from suncore.planner.run import PlannerRun, plan, run

__all__ = ["PlannerInputs", "PlannerRun", "PlannerWeights", "build_inputs", "plan", "run"]
