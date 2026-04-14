from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StringEnum(str, Enum):
    """Backport-friendly replacement for StrEnum."""

    def __str__(self) -> str:
        return self.value


class Severity(StringEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class ScopeType(StringEnum):
    GLOBAL = "GLOBAL"
    UNDERLYING = "UNDERLYING"
    ACCOUNT = "ACCOUNT"


class EventType(StringEnum):
    MARKET_SHOCK = "MARKET_SHOCK"
    VOL_SURFACE_CHANGED = "VOL_SURFACE_CHANGED"
    RATE_CURVE_CHANGED = "RATE_CURVE_CHANGED"
    DIVIDEND_CURVE_CHANGED = "DIVIDEND_CURVE_CHANGED"
    CORPORATE_ACTION_ANNOUNCED = "CORPORATE_ACTION_ANNOUNCED"
    CORPORATE_ACTION_EFFECTIVE = "CORPORATE_ACTION_EFFECTIVE"
    MARGIN_CONFIG_CHANGED = "MARGIN_CONFIG_CHANGED"
    OFFSET_MAPPING_CHANGED = "OFFSET_MAPPING_CHANGED"
    CONCENTRATION_CONFIG_CHANGED = "CONCENTRATION_CONFIG_CHANGED"
    POSITION_CHANGED = "POSITION_CHANGED"
    EXERCISE_ASSIGNMENT_CHANGED = "EXERCISE_ASSIGNMENT_CHANGED"
    ACCOUNT_TRANSFER_CHANGED = "ACCOUNT_TRANSFER_CHANGED"
    EOD_CYCLE_STARTED = "EOD_CYCLE_STARTED"
    MANUAL_RECALC_REQUESTED = "MANUAL_RECALC_REQUESTED"


class ArtifactType(StringEnum):
    VOL_SURFACE = "VOL_SURFACE"
    DIVIDEND_CURVE = "DIVIDEND_CURVE"
    RATE_CURVE = "RATE_CURVE"
    CONTRACT_RULES = "CONTRACT_RULES"
    PRICE_VOL_MATRIX = "PRICE_VOL_MATRIX"
    ACCOUNT_MARGIN = "ACCOUNT_MARGIN"
    MARGIN_SNAPSHOT = "MARGIN_SNAPSHOT"
    LIQUIDATION_ALERT = "LIQUIDATION_ALERT"


class InstrumentType(StringEnum):
    STOCK = "STOCK"
    OPTION = "OPTION"
    FUTURE = "FUTURE"


class OptionRight(StringEnum):
    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(StringEnum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


class ScenarioFamily(StringEnum):
    TIMS = "TIMS"
    BASE_RISK = "BASE_RISK"
    CONCENTRATION = "CONCENTRATION"


@dataclass(frozen=True)
class VersionBundle:
    market_data_version: str
    vol_surface_version: str
    dividend_curve_version: str
    rate_curve_version: str
    scenario_set_version: str
    margin_rule_version: str
    pricing_model_version: str


@dataclass(frozen=True)
class MarginEvent:
    event_id: str
    event_type: EventType
    priority: Severity
    scope: ScopeType
    effective_at: str
    source: str
    underlyings: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ImpactSet:
    artifacts: tuple[ArtifactType, ...]
    underlyings: tuple[str, ...]
    account_ids: tuple[str, ...]
    requires_curve_refresh: bool = False
    requires_matrix_refresh: bool = False
    requires_snapshot: bool = True


@dataclass(frozen=True)
class RecalcTask:
    task_id: str
    event_id: str
    priority: Severity
    scope: ScopeType
    artifacts: tuple[ArtifactType, ...]
    underlyings: tuple[str, ...]
    account_ids: tuple[str, ...]
    version_bundle: VersionBundle


@dataclass
class RiskConfig:
    tims_scenario_count: int = 10
    tims_scan_range_pct: float = 0.18
    base_price_shocks: tuple[float, ...] = (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15)
    base_vol_shocks: tuple[float, ...] = (-0.30, -0.15, 0.0, 0.15, 0.30)
    default_product_offset: float = 0.90
    default_portfolio_offset: float = 0.75
    product_group_offsets: dict[str, float] = field(default_factory=dict)
    portfolio_group_offsets: dict[str, float] = field(default_factory=dict)
    liquidation_threshold: float = 0.80
    concentration_penalty_scale: float = 0.80
    concentration_base_share: float = 0.35


@dataclass
class UnderlyingState:
    symbol: str
    spot: float
    base_vol: float
    rate: float
    dividend_yield: float
    product_group: str = "DEFAULT_PRODUCT"
    portfolio_group: str = "DEFAULT_PORTFOLIO"
    concentration_down_pct: float = 0.25
    concentration_up_pct: float = 0.20
    concentration_vol_up_pct: float = 0.40
    updated_at: str = ""


@dataclass
class AccountState:
    account_id: str
    cash_balance: float = 0.0
    liquidation_threshold: float = 0.80


@dataclass
class Position:
    position_id: str
    account_id: str
    underlying: str
    instrument_type: InstrumentType
    quantity: float
    multiplier: float = 1.0
    class_group: str = ""
    product_group: str = ""
    portfolio_group: str = ""
    option_right: OptionRight | None = None
    strike: float | None = None
    days_to_expiry: int | None = None
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN
    reference_price: float | None = None


@dataclass(frozen=True)
class ScenarioPoint:
    scenario_id: str
    family: ScenarioFamily
    underlying: str
    shock_spot_pct: float
    shock_vol_pct: float
    spot: float
    vol: float
    rate: float
    dividend_yield: float


@dataclass
class ScenarioMatrix:
    underlying: str
    family: ScenarioFamily
    version_bundle: VersionBundle
    points: tuple[ScenarioPoint, ...]
    built_at: str


@dataclass
class MarginSnapshot:
    account_id: str
    tims_margin: float
    base_risk_margin: float
    concentration_margin: float
    final_margin: float
    dominant_algorithm: str
    total_equity: float
    margin_utilization: float
    liquidation_required: bool
    version_bundle: VersionBundle
    calculated_at: str
    event_id: str
    details: dict[str, object] = field(default_factory=dict)
