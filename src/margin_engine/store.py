from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

from .domain import (
    AccountState,
    EventType,
    MarginEvent,
    MarginSnapshot,
    Position,
    RiskConfig,
    ScenarioFamily,
    ScenarioMatrix,
    UnderlyingState,
    VersionBundle,
)
from .orchestrator import ArtifactStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class InMemoryArtifactStore(ArtifactStore):
    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self.risk_config = risk_config or RiskConfig()
        self.underlyings: dict[str, UnderlyingState] = {}
        self.accounts: dict[str, AccountState] = {}
        self.positions_by_account: dict[str, list[Position]] = {}
        self.account_index_by_underlying: dict[str, set[str]] = defaultdict(set)
        self.matrices: dict[tuple[str, str], ScenarioMatrix] = {}
        self.snapshots: dict[str, MarginSnapshot] = {}
        self.tasks: list[object] = []
        self.events: list[MarginEvent] = []
        self.version_numbers = {
            "market_data": 1,
            "vol_surface": 1,
            "dividend_curve": 1,
            "rate_curve": 1,
            "scenario_set": 1,
            "margin_rule": 1,
            "pricing_model": 1,
        }

    def reset(self) -> None:
        self.__init__(risk_config=RiskConfig())

    def resolve_impacted_accounts(self, underlyings: tuple[str, ...], explicit_accounts: tuple[str, ...]) -> tuple[str, ...]:
        accounts = set(explicit_accounts)
        for underlying in underlyings:
            accounts.update(self.account_index_by_underlying.get(underlying, set()))
        return tuple(sorted(accounts))

    def latest_version_bundle(self, event: MarginEvent) -> VersionBundle:
        self._reserve_versions_for_event(event)
        return self.current_bundle()

    def current_bundle(self) -> VersionBundle:
        return VersionBundle(
            market_data_version="md_%04d" % self.version_numbers["market_data"],
            vol_surface_version="vs_%04d" % self.version_numbers["vol_surface"],
            dividend_curve_version="dc_%04d" % self.version_numbers["dividend_curve"],
            rate_curve_version="rc_%04d" % self.version_numbers["rate_curve"],
            scenario_set_version="ss_%04d" % self.version_numbers["scenario_set"],
            margin_rule_version="mr_%04d" % self.version_numbers["margin_rule"],
            pricing_model_version="pm_%04d" % self.version_numbers["pricing_model"],
        )

    def publish_recalc_task(self, task) -> None:
        self.tasks.append(task)

    def record_event(self, event: MarginEvent) -> None:
        self.events.append(event)

    def upsert_underlying(self, state: UnderlyingState) -> UnderlyingState:
        state.updated_at = state.updated_at or utc_now_iso()
        self.underlyings[state.symbol] = state
        return state

    def get_underlying(self, symbol: str) -> UnderlyingState:
        return self.underlyings[symbol]

    def list_underlyings(self) -> list[UnderlyingState]:
        return [self.underlyings[key] for key in sorted(self.underlyings)]

    def upsert_account(self, account: AccountState) -> AccountState:
        self.accounts[account.account_id] = account
        return account

    def get_account(self, account_id: str) -> AccountState:
        return self.accounts.setdefault(account_id, AccountState(account_id=account_id))

    def list_accounts(self) -> list[AccountState]:
        return [self.accounts[key] for key in sorted(self.accounts)]

    def replace_positions(self, account_id: str, positions: list[Position]) -> list[Position]:
        old_positions = self.positions_by_account.get(account_id, [])
        for position in old_positions:
            self.account_index_by_underlying[position.underlying].discard(account_id)

        normalized = []
        for position in positions:
            normalized.append(replace(position, account_id=account_id))
            self.account_index_by_underlying[position.underlying].add(account_id)
        self.positions_by_account[account_id] = normalized
        return normalized

    def get_positions(self, account_id: str) -> list[Position]:
        return list(self.positions_by_account.get(account_id, []))

    def get_account_underlyings(self, account_id: str) -> tuple[str, ...]:
        underlyings = {position.underlying for position in self.positions_by_account.get(account_id, [])}
        return tuple(sorted(underlyings))

    def set_matrix(self, matrix: ScenarioMatrix) -> None:
        self.matrices[(matrix.underlying, matrix.family.value)] = matrix

    def get_matrix(self, underlying: str, family: ScenarioFamily) -> ScenarioMatrix | None:
        return self.matrices.get((underlying, family.value))

    def persist_snapshot(self, snapshot: MarginSnapshot) -> MarginSnapshot:
        self.snapshots[snapshot.account_id] = snapshot
        return snapshot

    def get_snapshot(self, account_id: str) -> MarginSnapshot | None:
        return self.snapshots.get(account_id)

    def list_snapshots(self) -> list[MarginSnapshot]:
        return [self.snapshots[key] for key in sorted(self.snapshots)]

    def _reserve_versions_for_event(self, event: MarginEvent) -> None:
        event_type = event.event_type
        if event_type == EventType.MARKET_SHOCK:
            self.version_numbers["market_data"] += 1
            spot_move_pct = abs(float(event.payload.get("spot_move_pct", 0.0)))
            iv_move_abs = abs(float(event.payload.get("iv_move_abs", 0.0)))
            if spot_move_pct >= 0.03 or iv_move_abs >= 0.05:
                self.version_numbers["vol_surface"] += 1
        elif event_type == EventType.VOL_SURFACE_CHANGED:
            self.version_numbers["vol_surface"] += 1
        elif event_type == EventType.RATE_CURVE_CHANGED:
            self.version_numbers["rate_curve"] += 1
        elif event_type == EventType.DIVIDEND_CURVE_CHANGED:
            self.version_numbers["dividend_curve"] += 1
        elif event_type in {EventType.CORPORATE_ACTION_ANNOUNCED, EventType.CORPORATE_ACTION_EFFECTIVE}:
            self.version_numbers["market_data"] += 1
            self.version_numbers["vol_surface"] += 1
            self.version_numbers["dividend_curve"] += 1
        elif event_type == EventType.MARGIN_CONFIG_CHANGED:
            self.version_numbers["scenario_set"] += 1
            self.version_numbers["margin_rule"] += 1
        elif event_type == EventType.CONCENTRATION_CONFIG_CHANGED:
            self.version_numbers["scenario_set"] += 1
            self.version_numbers["margin_rule"] += 1
        elif event_type == EventType.OFFSET_MAPPING_CHANGED:
            self.version_numbers["margin_rule"] += 1

