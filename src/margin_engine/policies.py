from __future__ import annotations

from .domain import ArtifactType, EventType, ImpactSet, MarginEvent


class TriggerPolicyEngine:
    """Translate domain events into recomputation impact sets."""

    def plan_impact(self, event: MarginEvent) -> ImpactSet:
        if event.event_type == EventType.MARKET_SHOCK:
            return self._plan_market_shock(event)

        if event.event_type == EventType.VOL_SURFACE_CHANGED:
            return ImpactSet(
                artifacts=(
                    ArtifactType.VOL_SURFACE,
                    ArtifactType.PRICE_VOL_MATRIX,
                    ArtifactType.ACCOUNT_MARGIN,
                    ArtifactType.MARGIN_SNAPSHOT,
                ),
                underlyings=event.underlyings,
                account_ids=event.account_ids,
                requires_curve_refresh=True,
                requires_matrix_refresh=True,
            )

        if event.event_type in {EventType.RATE_CURVE_CHANGED, EventType.DIVIDEND_CURVE_CHANGED}:
            curve_artifact = (
                ArtifactType.RATE_CURVE
                if event.event_type == EventType.RATE_CURVE_CHANGED
                else ArtifactType.DIVIDEND_CURVE
            )
            return ImpactSet(
                artifacts=(
                    curve_artifact,
                    ArtifactType.PRICE_VOL_MATRIX,
                    ArtifactType.ACCOUNT_MARGIN,
                    ArtifactType.MARGIN_SNAPSHOT,
                ),
                underlyings=event.underlyings,
                account_ids=event.account_ids,
                requires_curve_refresh=True,
                requires_matrix_refresh=True,
            )

        if event.event_type in {
            EventType.CORPORATE_ACTION_ANNOUNCED,
            EventType.CORPORATE_ACTION_EFFECTIVE,
        }:
            return ImpactSet(
                artifacts=(
                    ArtifactType.CONTRACT_RULES,
                    ArtifactType.DIVIDEND_CURVE,
                    ArtifactType.VOL_SURFACE,
                    ArtifactType.PRICE_VOL_MATRIX,
                    ArtifactType.ACCOUNT_MARGIN,
                    ArtifactType.MARGIN_SNAPSHOT,
                ),
                underlyings=event.underlyings,
                account_ids=event.account_ids,
                requires_curve_refresh=True,
                requires_matrix_refresh=True,
            )

        if event.event_type in {
            EventType.MARGIN_CONFIG_CHANGED,
            EventType.OFFSET_MAPPING_CHANGED,
            EventType.CONCENTRATION_CONFIG_CHANGED,
        }:
            return ImpactSet(
                artifacts=(
                    ArtifactType.PRICE_VOL_MATRIX,
                    ArtifactType.ACCOUNT_MARGIN,
                    ArtifactType.MARGIN_SNAPSHOT,
                ),
                underlyings=event.underlyings,
                account_ids=event.account_ids,
                requires_curve_refresh=False,
                requires_matrix_refresh=True,
            )

        if event.event_type in {
            EventType.POSITION_CHANGED,
            EventType.EXERCISE_ASSIGNMENT_CHANGED,
            EventType.ACCOUNT_TRANSFER_CHANGED,
            EventType.MANUAL_RECALC_REQUESTED,
        }:
            return ImpactSet(
                artifacts=(
                    ArtifactType.ACCOUNT_MARGIN,
                    ArtifactType.MARGIN_SNAPSHOT,
                ),
                underlyings=event.underlyings,
                account_ids=event.account_ids,
                requires_curve_refresh=False,
                requires_matrix_refresh=False,
            )

        return ImpactSet(
            artifacts=(ArtifactType.ACCOUNT_MARGIN, ArtifactType.MARGIN_SNAPSHOT),
            underlyings=event.underlyings,
            account_ids=event.account_ids,
            requires_curve_refresh=False,
            requires_matrix_refresh=False,
        )

    def _plan_market_shock(self, event: MarginEvent) -> ImpactSet:
        spot_move_pct = abs(float(event.payload.get("spot_move_pct", 0.0)))
        iv_move_abs = abs(float(event.payload.get("iv_move_abs", 0.0)))
        needs_surface_refresh = spot_move_pct >= 0.03 or iv_move_abs >= 0.05

        artifacts: list[ArtifactType] = []
        if needs_surface_refresh:
            artifacts.append(ArtifactType.VOL_SURFACE)
        artifacts.extend(
            [
                ArtifactType.PRICE_VOL_MATRIX,
                ArtifactType.ACCOUNT_MARGIN,
                ArtifactType.MARGIN_SNAPSHOT,
            ]
        )
        return ImpactSet(
            artifacts=tuple(artifacts),
            underlyings=event.underlyings,
            account_ids=event.account_ids,
            requires_curve_refresh=needs_surface_refresh,
            requires_matrix_refresh=True,
        )
