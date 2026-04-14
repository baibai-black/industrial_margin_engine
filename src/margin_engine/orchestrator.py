from __future__ import annotations

from dataclasses import asdict
from typing import Protocol
from uuid import uuid4

from .domain import ArtifactType, MarginEvent, RecalcTask, Severity, VersionBundle
from .policies import TriggerPolicyEngine


class ArtifactStore(Protocol):
    def resolve_impacted_accounts(self, underlyings: tuple[str, ...], explicit_accounts: tuple[str, ...]) -> tuple[str, ...]:
        ...

    def latest_version_bundle(self, event: MarginEvent) -> VersionBundle:
        ...

    def publish_recalc_task(self, task: RecalcTask) -> None:
        ...


class MarginOrchestrator:
    """Build recomputation tasks in dependency order."""

    def __init__(self, store: ArtifactStore, policy_engine: TriggerPolicyEngine | None = None) -> None:
        self.store = store
        self.policy_engine = policy_engine or TriggerPolicyEngine()

    def handle_event(self, event: MarginEvent) -> RecalcTask:
        impact = self.policy_engine.plan_impact(event)
        accounts = self.store.resolve_impacted_accounts(impact.underlyings, impact.account_ids)
        version_bundle = self.store.latest_version_bundle(event)

        task = RecalcTask(
            task_id=f"task_{uuid4().hex}",
            event_id=event.event_id,
            priority=event.priority,
            scope=event.scope,
            artifacts=self._ordered_artifacts(impact.artifacts, priority=event.priority),
            underlyings=impact.underlyings,
            account_ids=accounts,
            version_bundle=version_bundle,
        )
        self.store.publish_recalc_task(task)
        return task

    def _ordered_artifacts(
        self,
        artifacts: tuple[ArtifactType, ...],
        priority: Severity,
    ) -> tuple[ArtifactType, ...]:
        # Dependency order matters more than event arrival order.
        order = {
            ArtifactType.RATE_CURVE: 10,
            ArtifactType.DIVIDEND_CURVE: 20,
            ArtifactType.VOL_SURFACE: 30,
            ArtifactType.CONTRACT_RULES: 40,
            ArtifactType.PRICE_VOL_MATRIX: 50,
            ArtifactType.ACCOUNT_MARGIN: 60,
            ArtifactType.MARGIN_SNAPSHOT: 70,
            ArtifactType.LIQUIDATION_ALERT: 80,
        }
        sorted_artifacts = tuple(sorted(artifacts, key=lambda item: order[item]))
        if priority == Severity.P0 and ArtifactType.LIQUIDATION_ALERT not in sorted_artifacts:
            return (*sorted_artifacts, ArtifactType.LIQUIDATION_ALERT)
        return sorted_artifacts

    @staticmethod
    def explain_task(task: RecalcTask) -> dict[str, object]:
        """Serialize a task for logs or API output."""
        return asdict(task)
