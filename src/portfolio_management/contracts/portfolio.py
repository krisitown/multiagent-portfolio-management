"""Portfolio snapshots retain absent collections and unsupported instruments."""

from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .common import (
    Availability,
    Currency,
    FinancialDecimal,
    Finding,
    JsonValue,
    PublicModel,
    QuantityValue,
    RecordId,
)


class ContractIdentity(PublicModel):
    instrument_id: RecordId
    instrument_type: RecordId
    currency: Currency
    symbol: str | None = None
    underlying_id: RecordId | None = None
    expiry: date | None = None
    option_right: Literal["put", "call"] | None = None
    strike: FinancialDecimal | None = Field(default=None, ge=0)
    multiplier: FinancialDecimal | None = Field(default=None, gt=0)
    deliverable: dict[str, JsonValue] | None = None
    adjustment: str | None = None
    venue: str | None = None
    provider_ids: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_option_identity(self) -> Self:
        if self.instrument_type == "option":
            missing = [
                name
                for name in ("underlying_id", "expiry", "option_right", "strike", "multiplier")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError("option identity missing: " + ", ".join(missing))
        return self


class CashBalance(PublicModel):
    currency: Currency
    availability: Availability
    amount: FinancialDecimal | None = None
    source: str | None = None
    as_of: AwareDatetime | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_amount(self) -> Self:
        if (self.availability == Availability.KNOWN) != (self.amount is not None):
            raise ValueError("known cash requires amount; other availability must omit amount")
        return self


class Position(PublicModel):
    position_id: RecordId
    contract: ContractIdentity
    quantity: FinancialDecimal
    currency: Currency
    valuation: QuantityValue | None = None
    cost_basis: QuantityValue | None = None
    encumbered_quantity: FinancialDecimal | None = None
    source: str | None = None
    as_of: AwareDatetime | None = None
    findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def currency_matches_identity(self) -> Self:
        if self.currency != self.contract.currency:
            raise ValueError("position currency must match contract currency")
        return self


class CashFlow(PublicModel):
    flow_id: RecordId
    occurred_at: AwareDatetime
    amount: FinancialDecimal
    currency: Currency
    kind: Literal["deposit", "withdrawal", "income", "fee", "other"]
    source: str


class BrokerCapacity(PublicModel):
    availability: Availability
    as_of: AwareDatetime | None = None
    source: str | None = None
    buying_power: QuantityValue | None = None
    maintenance_margin: QuantityValue | None = None
    excess_liquidity: QuantityValue | None = None
    initial_margin: QuantityValue | None = None
    estimated: bool = False
    reason: str | None = None


class PortfolioSnapshot(PublicModel):
    snapshot_id: RecordId
    account_alias: RecordId
    source: RecordId
    as_of: AwareDatetime
    fetched_at: AwareDatetime
    base_currency: Currency
    completeness: Literal["complete", "partial", "unknown"]
    positions: tuple[Position, ...] | None = None
    cash: tuple[CashBalance, ...] | None = None
    valuations: tuple[QuantityValue, ...] | None = None
    flows: tuple[CashFlow, ...] | None = None
    broker_capacity: BrokerCapacity | None = None
    previous_snapshot_ref: RecordId | None = None
    findings: tuple[Finding, ...] = ()
