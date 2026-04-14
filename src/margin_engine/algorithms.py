from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable

from .domain import (
    AccountState,
    Position,
    RiskConfig,
    ScenarioFamily,
    ScenarioMatrix,
    UnderlyingState,
)
from .pricing import position_market_value


@dataclass(frozen=True)
class MarginResult:
    algorithm: str
    requirement: float
    explanation: str
    worst_scenario_id: str
    details: dict[str, object] = field(default_factory=dict)


class MarginAlgorithm(ABC):
    name: str
    family: ScenarioFamily

    @abstractmethod
    def calculate(
        self,
        account: AccountState,
        positions: list[Position],
        underlyings: dict[str, UnderlyingState],
        matrices: dict[str, ScenarioMatrix],
        risk_config: RiskConfig,
    ) -> MarginResult:
        """Return a margin requirement for one algorithm."""


class ScenarioMarginAlgorithm(MarginAlgorithm):
    def calculate(
        self,
        account: AccountState,
        positions: list[Position],
        underlyings: dict[str, UnderlyingState],
        matrices: dict[str, ScenarioMatrix],
        risk_config: RiskConfig,
    ) -> MarginResult:
        scenario_ids = self._scenario_ids(matrices.values())
        if not scenario_ids:
            return MarginResult(
                algorithm=self.name,
                requirement=0.0,
                explanation="No scenario matrix was available for this account.",
                worst_scenario_id="N/A",
            )

        worst_loss = 0.0
        worst_scenario_id = scenario_ids[0]
        worst_details: dict[str, object] = {}

        for scenario_id in scenario_ids:
            loss, details = self._portfolio_loss_for_scenario(
                scenario_id,
                positions=positions,
                underlyings=underlyings,
                matrices=matrices,
                risk_config=risk_config,
            )
            if loss > worst_loss:
                worst_loss = loss
                worst_scenario_id = scenario_id
                worst_details = details

        return MarginResult(
            algorithm=self.name,
            requirement=round(worst_loss, 2),
            explanation=self._build_explanation(worst_scenario_id, worst_details),
            worst_scenario_id=worst_scenario_id,
            details=worst_details,
        )

    def _scenario_ids(self, matrices: Iterable[ScenarioMatrix]) -> list[str]:
        matrix = next(iter(matrices), None)
        if matrix is None:
            return []
        return [point.scenario_id for point in matrix.points]

    def _portfolio_loss_for_scenario(
        self,
        scenario_id: str,
        *,
        positions: list[Position],
        underlyings: dict[str, UnderlyingState],
        matrices: dict[str, ScenarioMatrix],
        risk_config: RiskConfig,
    ) -> tuple[float, dict[str, object]]:
        class_group_totals: dict[str, float] = {}
        class_metadata: dict[str, tuple[str, str]] = {}
        underlying_contributions: dict[str, float] = {}

        for position in positions:
            state = underlyings[position.underlying]
            current_value = position_market_value(position, state)
            matrix = matrices[position.underlying]
            scenario_point = next(point for point in matrix.points if point.scenario_id == scenario_id)
            scenario_value = position_market_value(position, state, spot=scenario_point.spot, vol=scenario_point.vol)
            pnl = scenario_value - current_value

            class_group = position.class_group or position.underlying
            product_group = position.product_group or state.product_group
            portfolio_group = position.portfolio_group or state.portfolio_group

            class_group_totals[class_group] = class_group_totals.get(class_group, 0.0) + pnl
            class_metadata[class_group] = (product_group, portfolio_group)
            underlying_contributions[position.underlying] = underlying_contributions.get(position.underlying, 0.0) + pnl

        product_group_totals: dict[str, float] = {}
        product_to_portfolio: dict[str, str] = {}
        for class_group, pnl in class_group_totals.items():
            product_group, portfolio_group = class_metadata[class_group]
            product_group_totals.setdefault(product_group, 0.0)
            product_group_totals[product_group] += pnl
            product_to_portfolio[product_group] = portfolio_group

        product_adjusted: dict[str, float] = {}
        for product_group in sorted(product_group_totals):
            class_values = [
                pnl
                for class_name, pnl in class_group_totals.items()
                if class_metadata[class_name][0] == product_group
            ]
            offset = risk_config.product_group_offsets.get(product_group, risk_config.default_product_offset)
            product_adjusted[product_group] = _apply_offset(class_values, offset)

        portfolio_adjusted: dict[str, float] = {}
        for product_group, adjusted_pnl in product_adjusted.items():
            portfolio_group = product_to_portfolio[product_group]
            portfolio_adjusted.setdefault(portfolio_group, 0.0)
            portfolio_adjusted[portfolio_group] += adjusted_pnl

        portfolio_final: dict[str, float] = {}
        for portfolio_group in sorted(portfolio_adjusted):
            peer_values = [
                adjusted_pnl
                for product_name, adjusted_pnl in product_adjusted.items()
                if product_to_portfolio[product_name] == portfolio_group
            ]
            offset = risk_config.portfolio_group_offsets.get(
                portfolio_group,
                risk_config.default_portfolio_offset,
            )
            portfolio_final[portfolio_group] = _apply_offset(peer_values, offset)

        total_portfolio_pnl = sum(portfolio_final.values())
        loss = max(0.0, -total_portfolio_pnl)
        return loss, {
            "scenario_id": scenario_id,
            "top_underlying_losses": _top_losses(underlying_contributions),
            "class_group_pnl": {key: round(value, 2) for key, value in class_group_totals.items()},
            "product_group_pnl": {key: round(value, 2) for key, value in product_adjusted.items()},
            "portfolio_group_pnl": {key: round(value, 2) for key, value in portfolio_final.items()},
        }

    def _build_explanation(self, worst_scenario_id: str, details: dict[str, object]) -> str:
        return "Worst loss came from scenario %s under %s." % (worst_scenario_id, self.name)


class TIMSAlgorithm(ScenarioMarginAlgorithm):
    name = "TIMS"
    family = ScenarioFamily.TIMS


class BaseRiskAlgorithm(ScenarioMarginAlgorithm):
    name = "BASE_RISK"
    family = ScenarioFamily.BASE_RISK


class ConcentrationRiskAlgorithm(ScenarioMarginAlgorithm):
    name = "CONCENTRATION_RISK"
    family = ScenarioFamily.CONCENTRATION

    def calculate(
        self,
        account: AccountState,
        positions: list[Position],
        underlyings: dict[str, UnderlyingState],
        matrices: dict[str, ScenarioMatrix],
        risk_config: RiskConfig,
    ) -> MarginResult:
        base_result = super().calculate(account, positions, underlyings, matrices, risk_config)
        gross_notional_by_underlying: dict[str, float] = {}
        total_notional = 0.0
        for position in positions:
            state = underlyings[position.underlying]
            current_value = abs(position_market_value(position, state))
            gross_notional_by_underlying[position.underlying] = gross_notional_by_underlying.get(position.underlying, 0.0) + current_value
            total_notional += current_value

        if total_notional <= 0.0:
            return base_result

        largest_share = max(gross_notional_by_underlying.values()) / total_notional
        surcharge_multiplier = 1.0 + max(
            0.0,
            (largest_share - risk_config.concentration_base_share) * risk_config.concentration_penalty_scale,
        )
        adjusted_requirement = round(base_result.requirement * surcharge_multiplier, 2)
        details = dict(base_result.details)
        details["largest_underlying_share"] = round(largest_share, 4)
        details["surcharge_multiplier"] = round(surcharge_multiplier, 4)
        return MarginResult(
            algorithm=self.name,
            requirement=adjusted_requirement,
            explanation=(
                "Worst loss came from scenario %s under %s, with concentration surcharge."
                % (base_result.worst_scenario_id, self.name)
            ),
            worst_scenario_id=base_result.worst_scenario_id,
            details=details,
        )


class MarginEngine:
    def __init__(self, algorithms: list[MarginAlgorithm] | None = None) -> None:
        self.algorithms = algorithms or [
            TIMSAlgorithm(),
            BaseRiskAlgorithm(),
            ConcentrationRiskAlgorithm(),
        ]

    def calculate_margin(
        self,
        account: AccountState,
        positions: list[Position],
        *,
        underlyings: dict[str, UnderlyingState],
        matrices_by_family: dict[ScenarioFamily, dict[str, ScenarioMatrix]],
        risk_config: RiskConfig,
    ) -> dict[str, object]:
        results = []
        for algorithm in self.algorithms:
            family_matrices = matrices_by_family.get(algorithm.family, {})
            results.append(
                algorithm.calculate(
                    account,
                    positions,
                    underlyings,
                    family_matrices,
                    risk_config,
                )
            )
        dominant = max(results, key=lambda item: item.requirement, default=None)
        return {
            "results": results,
            "final_margin": round(dominant.requirement if dominant else 0.0, 2),
            "dominant_algorithm": dominant.algorithm if dominant else "N/A",
        }


def _apply_offset(values: list[float], offset_ratio: float) -> float:
    losses = sum(value for value in values if value < 0.0)
    profits = sum(value for value in values if value > 0.0)
    if losses < 0.0:
        return losses + profits * offset_ratio
    return profits


def _top_losses(underlying_contributions: dict[str, float]) -> list[dict[str, object]]:
    items = sorted(underlying_contributions.items(), key=lambda item: item[1])
    result = []
    for underlying, pnl in items[:3]:
        result.append({"underlying": underlying, "scenario_pnl": round(pnl, 2)})
    return result
