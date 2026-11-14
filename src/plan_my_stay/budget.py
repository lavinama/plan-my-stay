"""The budget check: stay price against the total, with a reserve when the fee basis is unknown.

docs/PLAN.md: for now only the stay price counts against the budget, because transport
cost isn't calculated yet. A total whose fee basis the site doesn't show is labelled
`card_total_fees_unknown`; the check adds a fee reserve (a percentage the team picks) to
it and the Verifier answers `warn`.
"""

from dataclasses import dataclass

from plan_my_stay.models import CostBasis


@dataclass(frozen=True)
class BudgetCheck:
    effective_total: float
    reserve_applied: bool  # true -> the Verifier answers `warn`
    within_budget: bool


def check_budget(total: float, basis: str, *, budget: float, fee_reserve_pct: float) -> BudgetCheck:
    """Compare a stay total (plus the fee reserve when its fee basis is unknown) with the budget."""
    if fee_reserve_pct < 0:
        raise ValueError("fee_reserve_pct must not be negative")
    reserve_applied = basis in CostBasis.FEES_UNKNOWN
    effective = total * (1 + fee_reserve_pct) if reserve_applied else float(total)
    return BudgetCheck(effective_total=effective, reserve_applied=reserve_applied, within_budget=effective <= budget)
