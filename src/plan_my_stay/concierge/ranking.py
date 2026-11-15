"""The Concierge ranks hotels from what each specialist returned (docs/SEAT_PROTOCOL.md, section 5).

Three factors, each turned into a score from 0 to 1 where 1 is the best hotel:

- commute: the average minutes to the meetings that count for the stay (the Stay agent ranks by this too);
- gym: the distance in metres to the nearest gym (the Gym agent's answer);
- restaurants: the average rank of the restaurants within the walk radius, 1 being the best (the Restaurant agent's).

A score is `(best + offset) / (this + offset)` over the hotels: the best hotel gets 1, and a near tie stays a near
tie instead of being stretched to 0 and 1. A hotel with no data for a factor (no gym found, no restaurant in the
radius, or not reported) scores 0 on it, so it is ranked down, not dropped. A factor nobody has data for (its agent is
not in the run, or found nothing anywhere) is left out and the weights renormalise over the factors that remain.

The weights are first guesses to tune on real data: commute counts most because the meetings are the fixed points
(docs/PLAN.md). Pure functions: no I/O, inputs are never changed.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plan_my_stay.concierge.gate import average_commute
from plan_my_stay.fit import GymFitEntry, RankedStay, RestaurantFitEntry
from plan_my_stay.models import StayOption

FACTORS = ("commute", "gym", "restaurants")
TOP_N = 3  # the hotels the person is shown

_OFFSETS = {"commute": 1.0, "gym": 100.0, "restaurants": 0.0}  # minutes, metres, ranks (which start at 1)
_SCORE_DIGITS = 9  # equal scores up to float noise tie-break on commute, price and the Stay agent's order


@dataclass(frozen=True)
class Weights:
    commute: float = 0.5
    gym: float = 0.25
    restaurants: float = 0.25

    def __post_init__(self) -> None:
        values = (self.commute, self.gym, self.restaurants)
        if any(v < 0 or math.isnan(v) for v in values):
            raise ValueError("weights must not be negative")
        if sum(values) <= 0:
            raise ValueError("at least one weight must be positive")

    def of(self, factor: str) -> float:
        return float(getattr(self, factor))


def _commute(stay: StayOption, anchor_ids: Sequence[str]) -> float | None:
    if stay.average_commute_minutes is not None:
        return stay.average_commute_minutes
    return average_commute(stay, anchor_ids)


def _gym_distance(stay: StayOption, fits: Mapping[str, GymFitEntry] | None) -> float | None:
    entry = fits.get(stay.stay_id) if fits is not None else None
    return entry.nearest_gym.distance_m if entry is not None and entry.nearest_gym is not None else None


def _restaurant_rank(stay: StayOption, fits: Mapping[str, RestaurantFitEntry] | None) -> float | None:
    entry = fits.get(stay.stay_id) if fits is not None else None
    return entry.average_rank if entry is not None and entry.count > 0 else None


def _score(best: float, value: float | None, offset: float) -> float:
    return 0.0 if value is None else (best + offset) / (value + offset)


def _deciding(index: int, order: Sequence[int], scores: Sequence[Mapping[str, float]], weights: Mapping[str, float]) -> str:
    """What put a hotel ahead of the next one: the factor with the biggest weighted lead over it. The last hotel
    has nothing below it, so its strongest weighted factor is reported. Ties go to commute, gym, restaurants."""
    here = scores[order[index]]
    if not here:
        return "stay agent order"
    if index + 1 < len(order):
        nxt = scores[order[index + 1]]
        gaps = {f: weights[f] * (here[f] - nxt[f]) for f in here}
        if max(gaps.values()) > 0:
            return max(here, key=lambda f: (gaps[f], -FACTORS.index(f)))
    return max(here, key=lambda f: (weights[f] * here[f], -FACTORS.index(f)))


def rank_hotels(
    stays: Sequence[StayOption],
    *,
    anchor_ids: Sequence[str],
    gym_fits: Mapping[str, GymFitEntry] | None,
    restaurant_fits: Mapping[str, RestaurantFitEntry] | None,
    weights: Weights = Weights(),
    top_n: int = TOP_N,
) -> tuple[RankedStay, ...]:
    """The best `top_n` of `stays` (in the Stay agent's order) by the weighted factors. `gym_fits` and
    `restaurant_fits` map a stay id to that agent's answer; None means the agent is not in the run."""
    if not stays or top_n <= 0:
        return ()
    values = {
        "commute": [_commute(s, anchor_ids) for s in stays],
        "gym": [_gym_distance(s, gym_fits) for s in stays],
        "restaurants": [_restaurant_rank(s, restaurant_fits) for s in stays],
    }
    active = [f for f in FACTORS if weights.of(f) > 0 and any(v is not None for v in values[f])]
    weight = {f: weights.of(f) for f in active}
    best = {f: min(v for v in values[f] if v is not None) for f in active}
    per_factor = [{f: _score(best[f], values[f][i], _OFFSETS[f]) for f in active} for i in range(len(stays))]
    total = sum(weight.values())
    combined = [sum(weight[f] * s[f] for f in active) / total if active else 0.0 for s in per_factor]

    def sort_key(i: int) -> tuple[float, float, float, int]:
        commute = values["commute"][i]
        return (-round(combined[i], _SCORE_DIGITS), math.inf if commute is None else commute, stays[i].total_price, i)

    order = sorted(range(len(stays)), key=sort_key)
    return tuple(
        _ranked(rank, stays[i], combined[i], per_factor[i], values, i, gym_fits, restaurant_fits, _deciding(rank - 1, order, per_factor, weight))
        for rank, i in enumerate(order[:top_n], start=1)
    )


def _ranked(
    rank: int,
    stay: StayOption,
    score: float,
    scores: Mapping[str, float],
    values: Mapping[str, Sequence[float | None]],
    index: int,
    gym_fits: Mapping[str, GymFitEntry] | None,
    restaurant_fits: Mapping[str, RestaurantFitEntry] | None,
    deciding_factor: str,
) -> RankedStay:
    gym = gym_fits.get(stay.stay_id) if gym_fits is not None else None
    food = restaurant_fits.get(stay.stay_id) if restaurant_fits is not None else None
    return RankedStay(
        rank=rank,
        stay_id=stay.stay_id,
        zone_id=stay.zone_id,
        name=stay.name,
        total_price=stay.total_price,
        currency=stay.currency,
        cost_basis=stay.cost_basis,
        score=min(1.0, max(0.0, score)),
        average_commute_minutes=values["commute"][index],
        nearest_gym_m=values["gym"][index],
        nearest_gym_name=gym.nearest_gym.name if gym is not None and gym.nearest_gym is not None else None,
        restaurant_count=food.count if food is not None else None,
        restaurant_average_rank=values["restaurants"][index],
        deciding_factor=deciding_factor,
        scores=dict(scores),
    )


def keep_stay_order(stays: Sequence[StayOption], *, anchor_ids: Sequence[str], top_n: int = TOP_N) -> tuple[RankedStay, ...]:
    """With the Stay agent as the only specialist, keep its order: it ranks by average commute and the Concierge shows
    the first `top_n` of the hotels the budget leaves (docs/PLAN.md, "How hotels are ranked"). The score is only the
    commute against the best hotel's, so the reply can show the numbers behind each placement; it never re-sorts.
    No gym or restaurant number is claimed."""
    if not stays or top_n <= 0:
        return ()
    commutes = [_commute(s, anchor_ids) for s in stays]
    known = [c for c in commutes if c is not None]
    best = min(known) if known else None
    values: dict[str, Sequence[float | None]] = {"commute": commutes, "gym": [None] * len(stays), "restaurants": [None] * len(stays)}
    shown = []
    for index in range(min(top_n, len(stays))):
        score = 0.0 if best is None else _score(best, commutes[index], _OFFSETS["commute"])
        scores = {} if best is None else {"commute": score}
        shown.append(_ranked(index + 1, stays[index], score, scores, values, index, None, None, "commute"))
    return tuple(shown)
