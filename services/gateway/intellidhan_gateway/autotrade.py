"""Agent-mediated auto-trading intent queue.

IntelliDhan owns eligibility, risk policy, arming, idempotency, and audit state.
The external primary agent (Claude) owns Robinhood MCP authentication plus the
review/place tool calls.  No Robinhood credential or MCP session enters this
process.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_schemas.signals import Action, Alert, Vehicle


class AutomationMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    SUPERVISED = "SUPERVISED"
    ARMED = "ARMED"


class IntentStatus(str, Enum):
    SHADOW = "SHADOW"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    READY = "READY"
    CLAIMED = "CLAIMED"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


TERMINAL_STATUSES = {
    IntentStatus.SHADOW,
    IntentStatus.BLOCKED,
    IntentStatus.REJECTED,
    IntentStatus.FAILED,
    IntentStatus.CANCELLED,
    IntentStatus.EXPIRED,
    IntentStatus.CLOSED,
}


class SettingsStore(Protocol):
    def get_setting(self, key: str) -> Any | None: ...

    def put_setting(self, key: str, payload: Any) -> None: ...


class AutotradePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AutomationMode = AutomationMode.OFF
    armed_until: datetime | None = None
    min_confidence: float = Field(default=0.75, ge=0.50, le=0.95)
    max_dollar_risk_per_order: float = Field(default=250.0, gt=0)
    max_daily_dollar_risk: float = Field(default=500.0, gt=0)
    max_open_intents: int = Field(default=1, ge=1, le=20)
    allowed_symbols: list[str] = []
    allowed_strategies: list[str] = []
    allowed_modules: list[str] = []
    allow_options: bool = False
    require_explicit_calibration: bool = True
    agent: str = "claude"
    revision: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _guard_yaml_bool_mode(cls, data: Any) -> Any:
        """YAML 1.1 (PyYAML's default) parses bare `off`/`on`/`yes`/`no` as
        booleans, not strings — `mode: OFF` silently becomes `mode: False`.
        This bit config/autotrade.yaml in production: the whole gateway
        failed to start because AutotradeManager() loads this file at
        LiveLoop.__init__ time. Coerce the safe case (False -> "OFF", the
        fail-closed default) and reject the ambiguous case loudly instead
        of letting either produce a cryptic enum-validation error."""
        if isinstance(data, dict) and isinstance(data.get("mode"), bool):
            data = dict(data)
            if data["mode"] is False:
                data["mode"] = "OFF"
            else:
                raise ValueError(
                    'mode parsed as boolean True — YAML 1.1 treats bare on/yes/true '
                    'as booleans; quote the value explicitly, e.g. mode: "ARMED"'
                )
        return data

    @model_validator(mode="after")
    def validate_caps(self) -> "AutotradePolicy":
        if self.max_dollar_risk_per_order > self.max_daily_dollar_risk:
            raise ValueError("per-order risk cannot exceed daily risk")
        return self


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    alert_id: str
    created_at: datetime
    updated_at: datetime
    valid_until: datetime
    mode: AutomationMode
    status: IntentStatus
    reasons: list[str] = []
    symbol: str
    strategy: str
    module: str
    confidence: float
    dollar_risk: float
    order_plan: dict[str, Any]
    claim: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    revision: int = 1


class AutotradeManager:
    """Persistent intent state with deterministic alert→intent mapping."""

    def __init__(
        self,
        policy_path: str | Path = "config/autotrade.yaml",
        state_path: str | Path | None = None,
        state_store: SettingsStore | None = None,
    ) -> None:
        self.policy_path = Path(policy_path)
        self.state_path = Path(
            state_path or os.getenv("AUTOTRADE_STATE_PATH", "data/autotrade_state.json")
        )
        self.state_store = state_store
        self.policy = self._load_policy()
        self.intents: dict[str, ExecutionIntent] = self._load_state()

    def _load_policy(self) -> AutotradePolicy:
        if self.state_store is not None:
            persisted = self.state_store.get_setting("autotrade_policy")
            if persisted:
                return AutotradePolicy.model_validate(persisted)
        if not self.policy_path.exists():
            return AutotradePolicy()
        raw = yaml.safe_load(self.policy_path.read_text()) or {}
        return AutotradePolicy.model_validate(raw.get("autotrade", raw))

    def _load_state(self) -> dict[str, ExecutionIntent]:
        if self.state_store is not None:
            persisted = self.state_store.get_setting("autotrade_intents")
            if persisted:
                return {
                    item["intent_id"]: ExecutionIntent.model_validate(item)
                    for item in persisted.get("intents", [])
                }
        if not self.state_path.exists():
            return {}
        raw = json.loads(self.state_path.read_text())
        return {
            item["intent_id"]: ExecutionIntent.model_validate(item)
            for item in raw.get("intents", [])
        }

    def _persist_policy(self) -> None:
        if self.state_store is not None:
            self.state_store.put_setting(
                "autotrade_policy", self.policy.model_dump(mode="json")
            )
            return
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"autotrade": self.policy.model_dump(mode="json")}
        self.policy_path.write_text(yaml.safe_dump(payload, sort_keys=False))

    def _persist_state(self) -> None:
        payload = {
            "contract_version": "1.0",
            "intents": [
                item.model_dump(mode="json")
                for item in sorted(self.intents.values(), key=lambda x: x.created_at)
            ],
        }
        if self.state_store is not None:
            self.state_store.put_setting("autotrade_intents", payload)
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.state_path)

    def effective_mode(self, now: datetime | None = None) -> AutomationMode:
        now = now or datetime.now(timezone.utc)
        if self.policy.mode == AutomationMode.ARMED:
            if self.policy.armed_until is None or self.policy.armed_until <= now:
                return AutomationMode.OFF
        return self.policy.mode

    def update_policy(self, updates: dict[str, Any]) -> AutotradePolicy:
        now = datetime.now(timezone.utc)
        updates = dict(updates)
        arm_for_minutes = updates.pop("arm_for_minutes", None)
        base = self.policy.model_dump()
        base.update(updates)
        base["revision"] = self.policy.revision + 1
        base["updated_at"] = now
        requested_mode = AutomationMode(base["mode"])
        if requested_mode == AutomationMode.ARMED:
            if arm_for_minutes is None or not 1 <= int(arm_for_minutes) <= 480:
                raise ValueError("ARMED mode requires arm_for_minutes between 1 and 480")
            base["armed_until"] = now + timedelta(minutes=int(arm_for_minutes))
        else:
            base["armed_until"] = None
        candidate = AutotradePolicy.model_validate(base)
        if candidate.mode in {AutomationMode.SUPERVISED, AutomationMode.ARMED}:
            if not candidate.allowed_symbols:
                raise ValueError("live modes require a non-empty symbol allowlist")
            if not candidate.allowed_strategies:
                raise ValueError("live modes require a non-empty strategy allowlist")
            if not candidate.allowed_modules:
                raise ValueError("live modes require a non-empty module allowlist")
        candidate.allowed_symbols = sorted({x.upper() for x in candidate.allowed_symbols})
        candidate.allowed_strategies = sorted({x.upper() for x in candidate.allowed_strategies})
        candidate.allowed_modules = sorted({x.upper() for x in candidate.allowed_modules})
        self.policy = candidate
        self._persist_policy()
        return self.policy

    def on_alert(self, alert: Alert, now: datetime | None = None) -> ExecutionIntent | None:
        now = now or datetime.now(timezone.utc)
        intent_id = f"ati_{alert.alert_id}"
        if intent_id in self.intents:
            return self.intents[intent_id]
        mode = self.effective_mode(now)
        if self.policy.mode == AutomationMode.OFF:
            return None
        reasons = self._eligibility_reasons(alert, now)
        if self.policy.mode == AutomationMode.ARMED and mode != AutomationMode.ARMED:
            reasons.append("arming window expired")
        if reasons:
            status = IntentStatus.BLOCKED
        elif mode == AutomationMode.SHADOW:
            status = IntentStatus.SHADOW
        elif mode == AutomationMode.SUPERVISED:
            status = IntentStatus.AWAITING_APPROVAL
        elif mode == AutomationMode.ARMED:
            status = IntentStatus.READY
        else:
            return None
        intent = ExecutionIntent(
            intent_id=intent_id,
            alert_id=alert.alert_id,
            created_at=now,
            updated_at=now,
            valid_until=alert.valid_until,
            mode=self.policy.mode,
            status=status,
            reasons=reasons,
            symbol=alert.symbol,
            strategy=alert.strategy,
            module=alert.module.value,
            confidence=alert.confidence,
            dollar_risk=alert.dollar_risk,
            order_plan=self._order_plan(alert),
        )
        self.intents[intent_id] = intent
        self._persist_state()
        return intent

    def _eligibility_reasons(self, alert: Alert, now: datetime) -> list[str]:
        reasons: list[str] = []
        policy = self.policy
        if alert.valid_until <= now:
            reasons.append("alert expired")
        if policy.allowed_symbols and alert.symbol.upper() not in policy.allowed_symbols:
            reasons.append("symbol not allowlisted")
        if policy.allowed_strategies and alert.strategy.upper() not in policy.allowed_strategies:
            reasons.append("strategy not allowlisted")
        if policy.allowed_modules and alert.module.value.upper() not in policy.allowed_modules:
            reasons.append("module not allowlisted")
        if alert.confidence < policy.min_confidence:
            reasons.append("confidence below automation minimum")
        if alert.dollar_risk > policy.max_dollar_risk_per_order:
            reasons.append("per-order risk cap exceeded")
        if alert.vehicle == Vehicle.OPTION and not policy.allow_options:
            reasons.append("options automation disabled")
        if alert.action not in {Action.EQUITY_BUY, Action.BTO}:
            reasons.append("only long-opening orders are supported")
        calibration = CalibrationMap.load(alert.strategy)
        if policy.require_explicit_calibration:
            if not calibration.buckets:
                reasons.append("no calibration evidence on file")
            if calibration.meta.get("live_eligible") is not True:
                reasons.append("strategy is not explicitly live eligible")
        open_intents = [
            item for item in self.intents.values()
            if item.status not in TERMINAL_STATUSES and item.valid_until > now
        ]
        if len(open_intents) >= policy.max_open_intents:
            reasons.append("open-intent cap reached")
        day = now.date()
        reserved = sum(
            item.dollar_risk for item in self.intents.values()
            if item.created_at.date() == day
            and item.status in {
                IntentStatus.AWAITING_APPROVAL,
                IntentStatus.READY,
                IntentStatus.CLAIMED,
                IntentStatus.EXECUTED,
            }
        )
        if reserved + alert.dollar_risk > policy.max_daily_dollar_risk:
            reasons.append("daily automation risk cap exceeded")
        return reasons

    @staticmethod
    def _order_plan(alert: Alert) -> dict[str, Any]:
        instrument: dict[str, Any]
        if alert.vehicle == Vehicle.EQUITY:
            instrument = {
                "type": "EQUITY",
                "symbol": alert.symbol,
                "quantity": alert.equity_qty,
            }
        else:
            instrument = {
                "type": "OPTION",
                "contracts": alert.contracts,
                "legs": [leg.model_dump(mode="json") for leg in alert.legs],
            }
        return {
            "account_scope": "ROBINHOOD_AGENTIC_ONLY",
            "intent": "OPEN_LONG_WITH_PROTECTION",
            "instrument": instrument,
            "entry": {
                "order_type": "LIMIT",
                "limit_price": alert.entry_limit,
                "entry_zone": list(alert.entry_zone),
                "time_in_force": "DAY",
            },
            "protection": {
                "must_be_established": True,
                "stop_underlying": alert.stop_underlying,
                "stop_rule": alert.stop_rule,
                "take_profits": [tp.model_dump(mode="json") for tp in alert.take_profits],
                "management": alert.management,
            },
            "abort_if": [
                "MCP pre-trade review returns a blocking alert",
                "price is outside the entry zone",
                "protective exit cannot be established",
                "intent or alert has expired",
                "Robinhood account is not the dedicated Agentic account",
            ],
        }

    def approve(self, intent_id: str) -> ExecutionIntent:
        intent = self._get(intent_id)
        if intent.status != IntentStatus.AWAITING_APPROVAL:
            raise ValueError("only AWAITING_APPROVAL intents can be approved")
        if intent.valid_until <= datetime.now(timezone.utc):
            return self._transition(intent, IntentStatus.EXPIRED)
        return self._transition(intent, IntentStatus.READY)

    def reject(self, intent_id: str, reason: str) -> ExecutionIntent:
        intent = self._get(intent_id)
        if intent.status in TERMINAL_STATUSES:
            raise ValueError("intent is already terminal")
        intent.reasons.append(reason or "rejected by operator")
        return self._transition(intent, IntentStatus.REJECTED)

    def claim(self, intent_id: str, agent: str = "claude") -> ExecutionIntent:
        intent = self._get(intent_id)
        now = datetime.now(timezone.utc)
        if intent.valid_until <= now:
            return self._transition(intent, IntentStatus.EXPIRED)
        if intent.status == IntentStatus.CLAIMED:
            lease = (intent.claim or {}).get("lease_until")
            if lease and datetime.fromisoformat(lease) > now:
                raise ValueError("intent already has an active claim")
        elif intent.status != IntentStatus.READY:
            raise ValueError("only READY intents can be claimed")
        intent.claim = {
            "agent": agent,
            "claimed_at": now.isoformat(),
            "lease_until": (now + timedelta(minutes=2)).isoformat(),
        }
        return self._transition(intent, IntentStatus.CLAIMED)

    def record_receipt(self, intent_id: str, payload: dict[str, Any]) -> ExecutionIntent:
        intent = self._get(intent_id)
        try:
            status = IntentStatus(payload["status"])
        except (KeyError, ValueError) as exc:
            raise ValueError("receipt status is required and invalid") from exc
        allowed = {
            IntentStatus.CLAIMED: {
                IntentStatus.EXECUTED,
                IntentStatus.REJECTED,
                IntentStatus.FAILED,
                IntentStatus.CANCELLED,
            },
            IntentStatus.EXECUTED: {IntentStatus.CLOSED, IntentStatus.FAILED},
        }
        if status not in allowed.get(intent.status, set()):
            raise ValueError(f"invalid receipt transition {intent.status.value} → {status.value}")
        receipt = dict(payload)
        receipt["recorded_at"] = datetime.now(timezone.utc).isoformat()
        intent.receipt = receipt
        return self._transition(intent, status)

    def list_intents(self, status: str | None = None) -> list[ExecutionIntent]:
        self._expire_stale()
        items = sorted(self.intents.values(), key=lambda x: x.created_at, reverse=True)
        if status:
            wanted = IntentStatus(status)
            items = [item for item in items if item.status == wanted]
        return items

    def status(self) -> dict[str, Any]:
        items = self.list_intents()
        counts: dict[str, int] = {}
        for item in items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return {
            "contract_version": "1.0",
            "effective_mode": self.effective_mode().value,
            "policy": self.policy.model_dump(mode="json"),
            "counts": counts,
            "ready": counts.get(IntentStatus.READY.value, 0),
            "awaiting_approval": counts.get(IntentStatus.AWAITING_APPROVAL.value, 0),
            "agent": "claude",
            "broker": "Robinhood Trading MCP",
            "credentials_in_app": False,
        }

    def _expire_stale(self) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for intent in self.intents.values():
            if intent.status not in TERMINAL_STATUSES and intent.valid_until <= now:
                intent.status = IntentStatus.EXPIRED
                intent.updated_at = now
                intent.revision += 1
                changed = True
        if changed:
            self._persist_state()

    def _get(self, intent_id: str) -> ExecutionIntent:
        try:
            return self.intents[intent_id]
        except KeyError as exc:
            raise KeyError(f"unknown intent {intent_id}") from exc

    def _transition(self, intent: ExecutionIntent, status: IntentStatus) -> ExecutionIntent:
        intent.status = status
        intent.updated_at = datetime.now(timezone.utc)
        intent.revision += 1
        self._persist_state()
        return intent
