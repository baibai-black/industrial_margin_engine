from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from .algorithms import MarginEngine
from .domain import (
    AccountState,
    ArtifactType,
    EventType,
    ExerciseStyle,
    InstrumentType,
    MarginEvent,
    MarginSnapshot,
    OptionRight,
    Position,
    RiskConfig,
    ScenarioFamily,
    ScenarioMatrix,
    ScopeType,
    Severity,
    UnderlyingState,
)
from .orchestrator import MarginOrchestrator
from .pricing import build_scenario_points, position_market_value
from .store import InMemoryArtifactStore, utc_now_iso
from .utils import to_primitive


class MarginRuntime:
    def __init__(self, store: InMemoryArtifactStore | None = None, engine: MarginEngine | None = None) -> None:
        self.store = store or InMemoryArtifactStore()
        self.engine = engine or MarginEngine()
        self.orchestrator = MarginOrchestrator(self.store)

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "underlyings": len(self.store.underlyings),
            "accounts": len(self.store.accounts),
            "snapshots": len(self.store.snapshots),
            "events": len(self.store.events),
            "tasks": len(self.store.tasks),
        }

    def reset_demo_data(self) -> dict[str, object]:
        self.store.reset()
        self._seed_demo_underlyings()
        self._seed_demo_accounts_and_positions()
        for symbol in self.store.underlyings:
            self._build_matrices_for_underlying(symbol, version_bundle=self.store.current_bundle())
        for account in sorted(self.store.accounts):
            self._recalculate_account(account, event_id="demo_seed", version_bundle=self.store.current_bundle())
        return {
            "message": "Demo data loaded.",
            "accounts": [account.account_id for account in self.store.list_accounts()],
            "underlyings": [state.symbol for state in self.store.list_underlyings()],
        }

    def upsert_underlying(self, payload: dict[str, object]) -> dict[str, object]:
        symbol = str(payload["symbol"]).upper()
        current = self.store.underlyings.get(symbol)
        state = UnderlyingState(
            symbol=symbol,
            spot=float(payload.get("spot", current.spot if current else 100.0)),
            base_vol=float(payload.get("base_vol", current.base_vol if current else 0.25)),
            rate=float(payload.get("rate", current.rate if current else 0.02)),
            dividend_yield=float(payload.get("dividend_yield", current.dividend_yield if current else 0.0)),
            product_group=str(payload.get("product_group", current.product_group if current else "DEFAULT_PRODUCT")),
            portfolio_group=str(payload.get("portfolio_group", current.portfolio_group if current else "DEFAULT_PORTFOLIO")),
            concentration_down_pct=float(
                payload.get("concentration_down_pct", current.concentration_down_pct if current else 0.25)
            ),
            concentration_up_pct=float(
                payload.get("concentration_up_pct", current.concentration_up_pct if current else 0.20)
            ),
            concentration_vol_up_pct=float(
                payload.get("concentration_vol_up_pct", current.concentration_vol_up_pct if current else 0.40)
            ),
            updated_at=utc_now_iso(),
        )
        self.store.upsert_underlying(state)
        self._build_matrices_for_underlying(symbol, version_bundle=self.store.current_bundle())
        return to_primitive(state)

    def upsert_account(self, payload: dict[str, object]) -> dict[str, object]:
        account = AccountState(
            account_id=str(payload["account_id"]),
            cash_balance=float(payload.get("cash_balance", 0.0)),
            liquidation_threshold=float(
                payload.get("liquidation_threshold", self.store.risk_config.liquidation_threshold)
            ),
        )
        self.store.upsert_account(account)
        return to_primitive(account)

    def replace_positions(self, payload: dict[str, object]) -> dict[str, object]:
        account_id = str(payload["account_id"])
        account = self.store.get_account(account_id)
        if "cash_balance" in payload:
            account.cash_balance = float(payload["cash_balance"])
            self.store.upsert_account(account)

        positions = [self._parse_position(account_id, item) for item in payload.get("positions", [])]
        normalized_positions = self.store.replace_positions(account_id, positions)
        affected_underlyings = tuple(sorted({position.underlying for position in normalized_positions}))
        result = self.emit_event(
            {
                "event_type": EventType.POSITION_CHANGED.value,
                "priority": Severity.P0.value,
                "scope": ScopeType.ACCOUNT.value,
                "source": "positions-api",
                "account_ids": [account_id],
                "underlyings": list(affected_underlyings),
                "payload": {
                    "change_reason": payload.get("change_reason", "positions replaced"),
                },
            }
        )
        result["positions"] = to_primitive(normalized_positions)
        return result

    def emit_event(self, payload: dict[str, object]) -> dict[str, object]:
        event = self._parse_event(payload)
        if event.scope == ScopeType.GLOBAL:
            event = replace(
                event,
                underlyings=event.underlyings or tuple(sorted(self.store.underlyings)),
                account_ids=event.account_ids or tuple(sorted(self.store.accounts)),
            )
        self.store.record_event(event)
        self._apply_event_state_changes(event)
        task = self.orchestrator.handle_event(event)
        execution = self._execute_task(task)
        return {
            "event": to_primitive(event),
            "task": to_primitive(task),
            "execution": execution,
        }

    def get_snapshot(self, account_id: str) -> dict[str, object]:
        snapshot = self.store.get_snapshot(account_id)
        if snapshot is None:
            raise KeyError("No snapshot found for account %s" % account_id)
        return to_primitive(snapshot)

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        account = self.store.get_account(account_id)
        positions = self.store.get_positions(account_id)
        total_market_value = 0.0
        for position in positions:
            total_market_value += position_market_value(position, self.store.get_underlying(position.underlying))
        return {
            "account": to_primitive(account),
            "positions": to_primitive(positions),
            "portfolio_market_value": round(total_market_value, 2),
        }

    def get_matrix(self, symbol: str, family: str) -> dict[str, object]:
        matrix = self.store.get_matrix(symbol.upper(), ScenarioFamily(family))
        if matrix is None:
            raise KeyError("No matrix found for %s/%s" % (symbol, family))
        return to_primitive(matrix)

    def list_underlyings(self) -> dict[str, object]:
        return {"underlyings": to_primitive(self.store.list_underlyings())}

    def list_accounts(self) -> dict[str, object]:
        return {"accounts": to_primitive(self.store.list_accounts())}

    def list_snapshots(self) -> dict[str, object]:
        return {"snapshots": to_primitive(self.store.list_snapshots())}

    def list_tasks(self) -> dict[str, object]:
        return {"tasks": to_primitive(self.store.tasks[-20:])}

    def _execute_task(self, task) -> dict[str, object]:
        built_matrices = []
        snapshots = []
        alerts = []

        needed_underlyings = set(task.underlyings)
        for account_id in task.account_ids:
            needed_underlyings.update(self.store.get_account_underlyings(account_id))

        for artifact in task.artifacts:
            if artifact == ArtifactType.PRICE_VOL_MATRIX:
                for symbol in sorted(needed_underlyings):
                    self._build_matrices_for_underlying(symbol, version_bundle=task.version_bundle)
                    built_matrices.append(symbol)
            elif artifact == ArtifactType.ACCOUNT_MARGIN:
                for account_id in task.account_ids:
                    snapshot = self._recalculate_account(
                        account_id,
                        event_id=task.event_id,
                        version_bundle=task.version_bundle,
                    )
                    if snapshot is not None:
                        snapshots.append(snapshot)
            elif artifact == ArtifactType.MARGIN_SNAPSHOT:
                # Snapshots are persisted during account margin calculation.
                continue
            elif artifact == ArtifactType.LIQUIDATION_ALERT:
                for snapshot in snapshots:
                    if snapshot["liquidation_required"]:
                        alerts.append(
                            {
                                "account_id": snapshot["account_id"],
                                "final_margin": snapshot["final_margin"],
                                "margin_utilization": snapshot["margin_utilization"],
                            }
                        )

        return {
            "rebuilt_underlyings": sorted(set(built_matrices)),
            "snapshots": snapshots,
            "alerts": alerts,
        }

    def _build_matrices_for_underlying(self, symbol: str, version_bundle) -> None:
        if symbol not in self.store.underlyings:
            return
        state = self.store.get_underlying(symbol)
        for family in ScenarioFamily:
            points = build_scenario_points(state, family, self.store.risk_config)
            self.store.set_matrix(
                ScenarioMatrix(
                    underlying=symbol,
                    family=family,
                    version_bundle=version_bundle,
                    points=tuple(points),
                    built_at=utc_now_iso(),
                )
            )

    def _recalculate_account(self, account_id: str, *, event_id: str, version_bundle) -> dict[str, object] | None:
        positions = self.store.get_positions(account_id)
        if not positions:
            snapshot = MarginSnapshot(
                account_id=account_id,
                tims_margin=0.0,
                base_risk_margin=0.0,
                concentration_margin=0.0,
                final_margin=0.0,
                dominant_algorithm="N/A",
                total_equity=round(self.store.get_account(account_id).cash_balance, 2),
                margin_utilization=0.0,
                liquidation_required=False,
                version_bundle=version_bundle,
                calculated_at=utc_now_iso(),
                event_id=event_id,
                details={"note": "No positions."},
            )
            self.store.persist_snapshot(snapshot)
            return to_primitive(snapshot)

        account = self.store.get_account(account_id)
        underlyings = {}
        matrices_by_family: dict[ScenarioFamily, dict[str, ScenarioMatrix]] = {family: {} for family in ScenarioFamily}
        for symbol in sorted({position.underlying for position in positions}):
            if self.store.get_matrix(symbol, ScenarioFamily.TIMS) is None:
                self._build_matrices_for_underlying(symbol, version_bundle=version_bundle)
            underlyings[symbol] = self.store.get_underlying(symbol)
            for family in ScenarioFamily:
                matrix = self.store.get_matrix(symbol, family)
                if matrix is not None:
                    matrices_by_family[family][symbol] = matrix

        result = self.engine.calculate_margin(
            account,
            positions,
            underlyings=underlyings,
            matrices_by_family=matrices_by_family,
            risk_config=self.store.risk_config,
        )

        current_portfolio_value = 0.0
        for position in positions:
            current_portfolio_value += position_market_value(position, underlyings[position.underlying])
        total_equity = round(account.cash_balance + current_portfolio_value, 2)
        final_margin = float(result["final_margin"])
        utilization = 0.0 if total_equity <= 0.0 else round(final_margin / total_equity, 4)
        liquidation_threshold = account.liquidation_threshold or self.store.risk_config.liquidation_threshold

        result_by_name = {item.algorithm: item for item in result["results"]}
        snapshot = MarginSnapshot(
            account_id=account_id,
            tims_margin=round(result_by_name["TIMS"].requirement, 2),
            base_risk_margin=round(result_by_name["BASE_RISK"].requirement, 2),
            concentration_margin=round(result_by_name["CONCENTRATION_RISK"].requirement, 2),
            final_margin=round(final_margin, 2),
            dominant_algorithm=str(result["dominant_algorithm"]),
            total_equity=total_equity,
            margin_utilization=utilization,
            liquidation_required=utilization >= liquidation_threshold,
            version_bundle=version_bundle,
            calculated_at=utc_now_iso(),
            event_id=event_id,
            details={
                item.algorithm: {
                    "explanation": item.explanation,
                    "worst_scenario_id": item.worst_scenario_id,
                    "details": item.details,
                }
                for item in result["results"]
            },
        )
        self.store.persist_snapshot(snapshot)
        return to_primitive(snapshot)

    def _apply_event_state_changes(self, event: MarginEvent) -> None:
        if event.event_type == EventType.MARKET_SHOCK:
            self._apply_market_shock(event)
        elif event.event_type == EventType.VOL_SURFACE_CHANGED:
            for symbol in event.underlyings:
                state = self.store.get_underlying(symbol)
                if "new_base_vol" in event.payload:
                    state.base_vol = float(event.payload["new_base_vol"])
                if "vol_multiplier" in event.payload:
                    state.base_vol *= float(event.payload["vol_multiplier"])
                state.updated_at = utc_now_iso()
                self.store.upsert_underlying(state)
        elif event.event_type == EventType.RATE_CURVE_CHANGED:
            self._apply_curve_shift(event, field_name="rate", payload_key="new_rate", shift_key="rate_shift_abs")
        elif event.event_type == EventType.DIVIDEND_CURVE_CHANGED:
            self._apply_curve_shift(
                event,
                field_name="dividend_yield",
                payload_key="new_dividend_yield",
                shift_key="dividend_shift_abs",
            )
        elif event.event_type in {EventType.MARGIN_CONFIG_CHANGED, EventType.CONCENTRATION_CONFIG_CHANGED, EventType.OFFSET_MAPPING_CHANGED}:
            self._apply_config_change(event.payload)
        elif event.event_type == EventType.CORPORATE_ACTION_EFFECTIVE:
            self._apply_corporate_action(event)

    def _apply_market_shock(self, event: MarginEvent) -> None:
        for symbol in event.underlyings:
            state = self.store.get_underlying(symbol)
            if "new_spot" in event.payload:
                state.spot = float(event.payload["new_spot"])
            else:
                state.spot = max(state.spot * (1.0 + float(event.payload.get("spot_move_pct", 0.0))), 0.01)

            if "new_base_vol" in event.payload:
                state.base_vol = max(float(event.payload["new_base_vol"]), 0.01)
            elif "iv_move_abs" in event.payload:
                state.base_vol = max(state.base_vol + float(event.payload["iv_move_abs"]), 0.01)
            state.updated_at = utc_now_iso()
            self.store.upsert_underlying(state)

    def _apply_curve_shift(self, event: MarginEvent, *, field_name: str, payload_key: str, shift_key: str) -> None:
        symbols = event.underlyings or tuple(sorted(self.store.underlyings))
        for symbol in symbols:
            state = self.store.get_underlying(symbol)
            if payload_key in event.payload:
                setattr(state, field_name, float(event.payload[payload_key]))
            elif shift_key in event.payload:
                setattr(state, field_name, getattr(state, field_name) + float(event.payload[shift_key]))
            state.updated_at = utc_now_iso()
            self.store.upsert_underlying(state)

    def _apply_corporate_action(self, event: MarginEvent) -> None:
        split_ratio = float(event.payload.get("split_ratio", 1.0))
        cash_dividend = float(event.payload.get("cash_dividend", 0.0))

        for symbol in event.underlyings:
            if symbol not in self.store.underlyings:
                continue
            state = self.store.get_underlying(symbol)
            if split_ratio > 0.0 and split_ratio != 1.0:
                state.spot = max(state.spot / split_ratio, 0.01)
            if cash_dividend:
                state.spot = max(state.spot - cash_dividend, 0.01)
            state.updated_at = utc_now_iso()
            self.store.upsert_underlying(state)

            impacted_accounts = self.store.resolve_impacted_accounts((symbol,), ())
            for account_id in impacted_accounts:
                updated_positions = []
                for position in self.store.get_positions(account_id):
                    if position.underlying != symbol:
                        updated_positions.append(position)
                        continue
                    updated = position
                    if split_ratio > 0.0 and split_ratio != 1.0:
                        if position.instrument_type == InstrumentType.STOCK:
                            updated = replace(position, quantity=position.quantity * split_ratio)
                        elif position.instrument_type == InstrumentType.OPTION and position.strike is not None:
                            updated = replace(
                                position,
                                strike=position.strike / split_ratio,
                                multiplier=position.multiplier * split_ratio,
                            )
                    updated_positions.append(updated)
                self.store.replace_positions(account_id, updated_positions)

    def _apply_config_change(self, payload: dict[str, object]) -> None:
        config = self.store.risk_config
        if "default_product_offset" in payload:
            config.default_product_offset = float(payload["default_product_offset"])
        if "default_portfolio_offset" in payload:
            config.default_portfolio_offset = float(payload["default_portfolio_offset"])
        if "product_group_offsets" in payload:
            config.product_group_offsets = {
                str(key): float(value)
                for key, value in dict(payload["product_group_offsets"]).items()
            }
        if "portfolio_group_offsets" in payload:
            config.portfolio_group_offsets = {
                str(key): float(value)
                for key, value in dict(payload["portfolio_group_offsets"]).items()
            }
        if "tims_scan_range_pct" in payload:
            config.tims_scan_range_pct = float(payload["tims_scan_range_pct"])
        if "tims_scenario_count" in payload:
            config.tims_scenario_count = int(payload["tims_scenario_count"])
        if "base_price_shocks" in payload:
            config.base_price_shocks = tuple(float(value) for value in payload["base_price_shocks"])
        if "base_vol_shocks" in payload:
            config.base_vol_shocks = tuple(float(value) for value in payload["base_vol_shocks"])
        if "liquidation_threshold" in payload:
            config.liquidation_threshold = float(payload["liquidation_threshold"])
        if "concentration_penalty_scale" in payload:
            config.concentration_penalty_scale = float(payload["concentration_penalty_scale"])
        if "concentration_base_share" in payload:
            config.concentration_base_share = float(payload["concentration_base_share"])

    def _parse_event(self, payload: dict[str, object]) -> MarginEvent:
        event_type = EventType(str(payload["event_type"]))
        underlyings = tuple(str(item).upper() for item in payload.get("underlyings", []))
        account_ids = tuple(str(item) for item in payload.get("account_ids", []))
        return MarginEvent(
            event_id=str(payload.get("event_id", "evt_%s" % uuid4().hex)),
            event_type=event_type,
            priority=Severity(str(payload.get("priority", Severity.P1.value))),
            scope=ScopeType(str(payload.get("scope", self._default_scope(event_type)))),
            effective_at=str(payload.get("effective_at", utc_now_iso())),
            source=str(payload.get("source", "api")),
            underlyings=underlyings,
            account_ids=account_ids,
            payload=dict(payload.get("payload", {})),
        )

    def _parse_position(self, account_id: str, payload: dict[str, object]) -> Position:
        return Position(
            position_id=str(payload.get("position_id", "pos_%s" % uuid4().hex[:8])),
            account_id=account_id,
            underlying=str(payload["underlying"]).upper(),
            instrument_type=InstrumentType(str(payload["instrument_type"]).upper()),
            quantity=float(payload["quantity"]),
            multiplier=float(payload.get("multiplier", 100.0 if str(payload["instrument_type"]).upper() == "OPTION" else 1.0)),
            class_group=str(payload.get("class_group", payload.get("underlying", ""))).upper(),
            product_group=str(payload.get("product_group", "")),
            portfolio_group=str(payload.get("portfolio_group", "")),
            option_right=(
                OptionRight(str(payload["option_right"]).upper())
                if payload.get("option_right") is not None
                else None
            ),
            strike=float(payload["strike"]) if payload.get("strike") is not None else None,
            days_to_expiry=int(payload["days_to_expiry"]) if payload.get("days_to_expiry") is not None else None,
            exercise_style=ExerciseStyle(str(payload.get("exercise_style", "EUROPEAN")).upper()),
            reference_price=float(payload["reference_price"]) if payload.get("reference_price") is not None else None,
        )

    def _default_scope(self, event_type: EventType) -> str:
        if event_type in {
            EventType.POSITION_CHANGED,
            EventType.EXERCISE_ASSIGNMENT_CHANGED,
            EventType.ACCOUNT_TRANSFER_CHANGED,
            EventType.MANUAL_RECALC_REQUESTED,
        }:
            return ScopeType.ACCOUNT.value
        if event_type in {
            EventType.RATE_CURVE_CHANGED,
            EventType.MARGIN_CONFIG_CHANGED,
            EventType.EOD_CYCLE_STARTED,
        }:
            return ScopeType.GLOBAL.value
        return ScopeType.UNDERLYING.value

    def _seed_demo_underlyings(self) -> None:
        for payload in [
            {
                "symbol": "AAPL",
                "spot": 185.0,
                "base_vol": 0.28,
                "rate": 0.03,
                "dividend_yield": 0.005,
                "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                "portfolio_group": "US_EQUITY_DERIVATIVES",
            },
            {
                "symbol": "TSLA",
                "spot": 172.0,
                "base_vol": 0.55,
                "rate": 0.03,
                "dividend_yield": 0.0,
                "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                "portfolio_group": "US_EQUITY_DERIVATIVES",
                "concentration_down_pct": 0.32,
                "concentration_up_pct": 0.25,
                "concentration_vol_up_pct": 0.55,
            },
            {
                "symbol": "SPY",
                "spot": 520.0,
                "base_vol": 0.19,
                "rate": 0.03,
                "dividend_yield": 0.012,
                "product_group": "US_INDEX_OPTIONS",
                "portfolio_group": "US_EQUITY_DERIVATIVES",
            },
        ]:
            self.upsert_underlying(payload)

    def _seed_demo_accounts_and_positions(self) -> None:
        self.upsert_account({"account_id": "ACC10001", "cash_balance": 250000.0, "liquidation_threshold": 0.85})
        self.upsert_account({"account_id": "ACC20001", "cash_balance": 150000.0, "liquidation_threshold": 0.80})

        self.store.replace_positions(
            "ACC10001",
            [
                self._parse_position(
                    "ACC10001",
                    {
                        "position_id": "aapl_stock",
                        "underlying": "AAPL",
                        "instrument_type": "STOCK",
                        "quantity": 1200,
                        "multiplier": 1,
                        "class_group": "AAPL",
                        "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                    },
                ),
                self._parse_position(
                    "ACC10001",
                    {
                        "position_id": "aapl_call_short",
                        "underlying": "AAPL",
                        "instrument_type": "OPTION",
                        "quantity": -40,
                        "multiplier": 100,
                        "class_group": "AAPL",
                        "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                        "option_right": "CALL",
                        "strike": 195,
                        "days_to_expiry": 45,
                        "exercise_style": "AMERICAN",
                    },
                ),
                self._parse_position(
                    "ACC10001",
                    {
                        "position_id": "spy_put_long",
                        "underlying": "SPY",
                        "instrument_type": "OPTION",
                        "quantity": 25,
                        "multiplier": 100,
                        "class_group": "SPY",
                        "product_group": "US_INDEX_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                        "option_right": "PUT",
                        "strike": 500,
                        "days_to_expiry": 70,
                    },
                ),
            ],
        )

        self.store.replace_positions(
            "ACC20001",
            [
                self._parse_position(
                    "ACC20001",
                    {
                        "position_id": "tsla_call_short",
                        "underlying": "TSLA",
                        "instrument_type": "OPTION",
                        "quantity": -55,
                        "multiplier": 100,
                        "class_group": "TSLA",
                        "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                        "option_right": "CALL",
                        "strike": 190,
                        "days_to_expiry": 28,
                    },
                ),
                self._parse_position(
                    "ACC20001",
                    {
                        "position_id": "tsla_put_short",
                        "underlying": "TSLA",
                        "instrument_type": "OPTION",
                        "quantity": -35,
                        "multiplier": 100,
                        "class_group": "TSLA",
                        "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                        "option_right": "PUT",
                        "strike": 145,
                        "days_to_expiry": 28,
                    },
                ),
                self._parse_position(
                    "ACC20001",
                    {
                        "position_id": "tsla_stock_hedge",
                        "underlying": "TSLA",
                        "instrument_type": "STOCK",
                        "quantity": 600,
                        "multiplier": 1,
                        "class_group": "TSLA",
                        "product_group": "US_TECH_SINGLE_STOCK_OPTIONS",
                        "portfolio_group": "US_EQUITY_DERIVATIVES",
                    },
                ),
            ],
        )
