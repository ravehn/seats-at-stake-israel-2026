"""Design of the measurement of a polling-place signage effect: power simulated on the real station panel.

The question (docs/design/measurement-design-2026-11.md): what does one-sided outdoor campaigning at the fence
of a polling building do to the vote shares counted inside it? The retrospective comparisons of this project
(`link.city_effect`, `link.exposure`) cannot isolate it — the decision to clear a locality follows the same
gradient as the swing itself (docs/research/grill-round4-facts-2026-10-31.md § 0). A prospective experiment can,
and before anyone opens a door it has to be simulated on the real panel of buildings:

- `panel`       the population — buildings (ריכוז) with their matched stations of two elections, swings,
                baseline covariates and strata;
- `effect`      a treatment effect written into the real counts of the current election, vote by vote
                (switch between blocs or abstention), keeping every arithmetic identity;
- `estimators`  the pre-specified analysis — stratum fixed effects, baseline covariates, robust errors;
                intention-to-treat and the complier average (instrumental-variable) effect;
- `simulate`    thousands of random samples, assignments and injections → power, minimum detectable effect,
                interval coverage; the normal approximation of the planning note for comparison;
- `retro`       the sensitivity of the existing retrospective design (matched twin cities) to an injected
                city-wide effect;
- `report`      tables and figures (project theme) with the data version.

Nothing here is an estimate of what happened in 2026: every effect is injected, every result says how well a
future design would see an effect of a given size.
"""

from electionforensics.design.panel import Panel, build_panel, load_panel
from electionforensics.design.simulate import Scenario, normal_approx_n, simulate, simulate_grid

__all__ = ["Panel", "Scenario", "build_panel", "load_panel", "normal_approx_n", "simulate", "simulate_grid"]
