"""What every scorer shares: axis values with reasons, and the weighted total."""

from typing import NamedTuple

from mozg_outreach.domain import ScoreAxis


class AxisValue(NamedTuple):
    value: float | None
    reason: str


def weighted_axes(values: dict[str, AxisValue], weights: dict[str, int]) -> list[ScoreAxis]:
    if sum(weights.values()) <= 0:
        raise ValueError(f"scoring weights sum to {sum(weights.values())}; one must be > 0")
    return [
        ScoreAxis(axis=name, value=value, weight=weights[name], reason=reason)
        for name, (value, reason) in values.items()
    ]


def weighted_total(axes: list[ScoreAxis]) -> float | None:
    """Mean over assessed axes only, so an axis without data neither helps nor hurts."""
    assessed = [(a.value, a.weight) for a in axes if a.value is not None]
    weight = sum(w for _, w in assessed)
    return round(sum(v * w for v, w in assessed) / weight, 1) if weight else None
