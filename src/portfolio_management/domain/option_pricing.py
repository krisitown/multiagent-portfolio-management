"""Public hypothetical position math; no quote, execution or probability inference."""

import math
from datetime import date
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from portfolio_management.domain.money import arithmetic_context

Number = Annotated[Decimal, Field(allow_inf_nan=False, ge=0, le=1_000_000_000)]
Signed = Annotated[Decimal, Field(allow_inf_nan=False, ge=-1_000_000_000, le=1_000_000_000)]


class ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def exact_numbers(cls, value: Any) -> Any:
        if isinstance(value, (float, bool)):
            raise ValueError("Use exact decimal strings or integers, never floats/booleans")
        return value


class OptionLeg(ExactModel):
    leg_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    option_right: Literal["call", "put"]
    strike: Number = Field(gt=0)
    expiry: date
    quantity: int = Field(strict=True, ge=-1000000, le=1000000)
    multiplier: Number = Field(gt=0, le=1000000)
    premium: Number
    exercise_style: Literal["european", "american"]
    deliverable: Literal["standard"]

    @model_validator(mode="after")
    def nonzero(self) -> Self:
        if self.quantity == 0:
            raise ValueError("Option quantity must be a signed nonzero whole contract count")
        return self


class PriceScenario(ExactModel):
    label: str = Field(min_length=1, max_length=80)
    spot: Number
    as_of: date
    volatility: Number | None = Field(default=None, le=10)
    leg_volatilities: dict[str, Number] = Field(default_factory=dict)
    rate: Signed | None = Field(default=None, ge=-1, le=1)
    dividend_yield: Number | None = Field(default=None, le=1)

    @field_validator("leg_volatilities", mode="before")
    @classmethod
    def exact_volatilities(cls, value: Any) -> Any:
        if not isinstance(value, dict) or any(isinstance(v, (float, bool)) for v in value.values()):
            raise ValueError("Per-leg volatilities require decimal strings")
        return value


class ScenarioRequest(ExactModel):
    """One standard underlying/currency, signed legs, explicitly supplied model inputs."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valuation_date: date
    stock_shares: Signed = Decimal(0)
    stock_entry_price: Number | None = None
    entry_fees: Number = Decimal(0)
    exit_fees: Number = Decimal(0)
    legs: list[OptionLeg] = Field(default_factory=list, max_length=16)
    scenarios: list[PriceScenario] = Field(min_length=1, max_length=40)
    tree_steps: int = Field(default=300, strict=True, ge=25, le=1000)

    @model_validator(mode="after")
    def supported(self) -> Self:
        ids = {leg.leg_id for leg in self.legs}
        if len(ids) != len(self.legs):
            raise ValueError("Leg identities must be unique")
        if not self.legs and not self.stock_shares:
            raise ValueError("At least one option or stock exposure is required")
        if self.stock_shares and self.stock_entry_price is None:
            raise ValueError("Stock exposure requires stock_entry_price")
        modeled_american = 0
        for leg in self.legs:
            if not 0 <= (leg.expiry - self.valuation_date).days <= 365 * 30:
                raise ValueError("Expiries must be on/after valuation_date and within 30 years")
        for scenario in self.scenarios:
            if scenario.as_of < self.valuation_date:
                raise ValueError("Scenario precedes valuation_date")
            if set(scenario.leg_volatilities) - ids:
                raise ValueError("Unknown leg ID in per-leg volatilities")
            if any(v > 10 for v in scenario.leg_volatilities.values()):
                raise ValueError("Volatility exceeds supported bound 10")
            for leg in self.legs:
                if scenario.as_of > leg.expiry:
                    raise ValueError(
                        "Scenario beyond a leg expiry needs path/settlement assumptions"
                    )
                if scenario.as_of < leg.expiry:
                    if (
                        scenario.rate is None
                        or scenario.dividend_yield is None
                        or (
                            scenario.volatility is None
                            and leg.leg_id not in scenario.leg_volatilities
                        )
                    ):
                        raise ValueError("Pre-expiry legs require IV, rate and dividend_yield")
                    modeled_american += leg.exercise_style == "american"
        if modeled_american * self.tree_steps**2 > 12_000_000:
            raise ValueError("American tree workload exceeds bound; reduce scenarios/legs/steps")
        return self


def option_price(
    right: str,
    spot: float,
    strike: float,
    years: float,
    volatility: float,
    rate: float,
    dividend_yield: float,
    style: str,
    steps: int,
) -> float:
    """BSM European or CRR American; rates/yield continuous, ACT/365, binary64."""
    values = (spot, strike, years, volatility, rate, dividend_yield)
    if (
        not all(math.isfinite(v) for v in values)
        or spot < 0
        or strike <= 0
        or years < 0
        or volatility < 0
        or right not in {"call", "put"}
        or style not in {"european", "american"}
        or not 25 <= steps <= 1000
    ):
        raise ValueError("Unsupported pricing inputs")
    sign = 1 if right == "call" else -1

    def intrinsic(value: float) -> float:
        return max(sign * (value - strike), 0.0)

    if years == 0:
        return intrinsic(spot)
    if style == "european":
        discounted_spot = spot * math.exp(-dividend_yield * years)
        discounted_strike = strike * math.exp(-rate * years)
        if spot == 0 or volatility == 0:
            return max(sign * (discounted_spot - discounted_strike), 0.0)
        sigma_t = volatility * math.sqrt(years)
        d1 = (
            math.log(spot / strike) + (rate - dividend_yield + volatility**2 / 2) * years
        ) / sigma_t
        d2 = d1 - sigma_t

        def normal(x: float) -> float:
            return math.erfc(-x / math.sqrt(2)) / 2

        return max(
            sign * (discounted_spot * normal(sign * d1) - discounted_strike * normal(sign * d2)),
            0.0,
        )
    dt = years / steps
    discount = math.exp(-rate * dt)
    if volatility == 0 or spot == 0:
        # Deterministic path: best exercise time on the stated finite tree grid.
        return max(
            math.exp(-rate * i * dt) * intrinsic(spot * math.exp((rate - dividend_yield) * i * dt))
            for i in range(steps + 1)
        )
    u = math.exp(volatility * math.sqrt(dt))
    d = 1 / u
    probability = (math.exp((rate - dividend_yield) * dt) - d) / (u - d)
    if not 0 <= probability <= 1:
        raise ValueError("CRR risk-neutral probability outside [0,1]; change steps/inputs")
    values_at_nodes = [intrinsic(spot * u ** (2 * j - steps)) for j in range(steps + 1)]
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            continuation = discount * (
                probability * values_at_nodes[j + 1] + (1 - probability) * values_at_nodes[j]
            )
            values_at_nodes[j] = max(continuation, intrinsic(spot * u ** (2 * j - i)))
    result = values_at_nodes[0]
    if not math.isfinite(result):
        raise ValueError("Pricing overflow; inputs exceed numerical model scope")
    return result


def scenario_values(request: ScenarioRequest) -> dict[str, Any]:
    """Decimal cash/payoff math; modeled per-share estimates converted from binary64."""
    with localcontext(arithmetic_context()):
        stock_basis = request.stock_shares * (request.stock_entry_price or Decimal(0))
        premium = sum(
            (leg.quantity * leg.multiplier * leg.premium for leg in request.legs), Decimal(0)
        )
        entry_cash = -stock_basis - premium - request.entry_fees

        def terminal_pnl(spot: Decimal) -> Decimal:
            value = request.stock_shares * spot
            for leg in request.legs:
                intrinsic = max(
                    (spot - leg.strike) if leg.option_right == "call" else (leg.strike - spot),
                    Decimal(0),
                )
                value += leg.quantity * leg.multiplier * intrinsic
            return entry_cash + value - request.exit_fees

        expiries = {leg.expiry for leg in request.legs}
        risk: dict[str, Any] = {
            "scope": "common_expiry_terminal_payoff_only; no early assignment, "
            "margin, borrow, dividends or financing costs"
        }
        if len(expiries) > 1:
            risk.update(
                status="unsupported_mixed_expiry_path",
                max_loss=None,
                loss_unbounded=None,
                max_profit=None,
                profit_unbounded=None,
            )
        else:
            knots = {Decimal(0), *(leg.strike for leg in request.legs)}
            pnl = [terminal_pnl(spot) for spot in knots]
            slope = request.stock_shares + sum(
                (
                    leg.quantity * leg.multiplier
                    for leg in request.legs
                    if leg.option_right == "call"
                ),
                Decimal(0),
            )
            risk.update(
                status="calculated",
                loss_unbounded=slope < 0,
                profit_unbounded=slope > 0,
                max_loss=None if slope < 0 else str(max(-min(pnl), Decimal(0))),
                max_profit=None if slope > 0 else str(max(pnl)),
            )
        scenarios = []
        for scenario in request.scenarios:
            leg_values = []
            position_value = request.stock_shares * scenario.spot
            for leg in request.legs:
                remaining = (leg.expiry - scenario.as_of).days
                volatility = scenario.leg_volatilities.get(leg.leg_id, scenario.volatility)
                if remaining == 0:
                    price = max(
                        (scenario.spot - leg.strike)
                        if leg.option_right == "call"
                        else (leg.strike - scenario.spot),
                        Decimal(0),
                    )
                    method = "expiry_intrinsic"
                else:
                    assert volatility is not None
                    assert scenario.rate is not None and scenario.dividend_yield is not None
                    price = Decimal(
                        str(
                            option_price(
                                leg.option_right,
                                float(scenario.spot),
                                float(leg.strike),
                                remaining / 365,
                                float(volatility),
                                float(scenario.rate),
                                float(scenario.dividend_yield),
                                leg.exercise_style,
                                request.tree_steps,
                            )
                        )
                    )
                    method = (
                        "american_crr_estimate"
                        if leg.exercise_style == "american"
                        else "european_bsm_estimate"
                    )
                value = leg.quantity * leg.multiplier * price
                position_value += value
                leg_values.append(
                    {
                        "leg_id": leg.leg_id,
                        "price_per_share": str(price),
                        "signed_value": str(value),
                        "method": method,
                        "days_remaining": remaining,
                        "volatility": str(volatility) if volatility is not None else None,
                    }
                )
            scenarios.append(
                {
                    "label": scenario.label,
                    "as_of": scenario.as_of.isoformat(),
                    "spot": str(scenario.spot),
                    "leg_values": leg_values,
                    "position_value": str(position_value),
                    "exit_cash_after_fees": str(position_value - request.exit_fees),
                    "pnl": str(entry_cash + position_value - request.exit_fees),
                }
            )
    return {
        "currency": request.currency,
        "entry_cash": str(entry_cash),
        "net_entry_debit": str(-entry_cash),
        "terminal_risk": risk,
        "scenarios": scenarios,
        "assumptions": {
            "model": "BSM European; CRR American finite tree",
            "tree_steps": request.tree_steps,
            "day_count": "ACT/365 calendar dates",
            "rates": "annual continuous compounding",
            "dividends": "continuous yield in option model only; stock dividends excluded",
            "numerics": "cash/intrinsic Decimal28; model binary64, no cent rounding",
            "entry": "premium currency/share; quantity signed contracts; fees total currency",
            "unsupported": "adjusted deliverables, discrete dividends, post-leg-expiry paths, "
            "FX, borrow/financing/margin, exercise settlement/assignment costs",
            "interpretation": "hypothetical model estimates, not quotes or executable prices; "
            "scenario likelihoods and broker capital requirements unknown",
        },
    }
