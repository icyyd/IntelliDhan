"""Agent-mediated auto-trading intent queue.

IntelliDhan owns eligibility, risk policy, arming, idempotency, and audit state.
The external primary agent (OpenAI Codex) owns Robinhood MCP authentication plus
the review/place tool calls. No Robinhood credential or MCP session enters this
process.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_schemas.signals import Action, Alert, Vehicle


AUTOTRADE_CONTRACT_VERSION = "2.0"
EXECUTION_AGENT = "codex"
CAPITAL_REVIEW_MAX_AGE_SECONDS = 120
ET = ZoneInfo("America/New_York")


class AutomationMode(str, Enum):
    SIMULATION = "SIMULATION"
    LIVE = "LIVE"


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
    IntentStatus.BLOCKED,
    IntentStatus.REJECTED,
    IntentStatus.FAILED,
    IntentStatus.CANCELLED,
    IntentStatus.EXPIRED,
    IntentStatus.CLOSED,
}

BLOCKABLE_STATUSES = {
    IntentStatus.SHADOW,
    IntentStatus.AWAITING_APPROVAL,
    IntentStatus.READY,
}


class SettingsStore(Protocol):
    def get_setting(self, key: str) -> Any | None: ...

    def put_setting(self, key: str, payload: Any) -> None: ...

    def append_autotrade_event(self, intent_id: str, payload: dict[str, Any]) -> None: ...

    def list_autotrade_events(self, intent_id: str | None = None) -> list[dict[str, Any]]: ...


class AutotradePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2.0"] = "2.0"
    mode: AutomationMode = AutomationMode.SIMULATION
    live_until: datetime | None = None
    min_confidence: float = Field(default=0.75, ge=0.50, le=0.95)
    max_dollar_risk_per_order: float = Field(default=250.0, gt=0)
    max_daily_dollar_risk: float = Field(default=500.0, gt=0)
    max_open_intents: int = Field(default=1, ge=1, le=20)
    # Size to the largest whole-contract position inside every active cap.
    max_available_capital_fraction: float = Field(default=0.80, gt=0, le=0.80)
    allowed_symbols: list[str] = ["QQQ", "SPY"]
    allowed_strategies: list[str] = ["EMA9_MTF_0DTE"]
    allowed_modules: list[str] = ["0DTE"]
    allow_options: bool = True
    option_expiry_days: list[int] = [0, 1]
    min_option_volume: int = Field(default=100, ge=0)
    min_option_open_interest: int = Field(default=500, ge=0)
    max_option_spread_fraction: float = Field(default=0.10, gt=0, le=0.25)
    max_option_quote_age_seconds: int = Field(default=30, ge=5, le=120)
    require_explicit_calibration: bool = True
    agent: Literal["codex"] = "codex"
    revision: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _guard_yaml_bool_mode(cls, data: Any) -> Any:
        """Fail closed when legacy YAML boolean spellings are encountered."""
        if isinstance(data, dict) and isinstance(data.get("mode"), bool):
            data = dict(data)
            if data["mode"] is False:
                data["mode"] = "SIMULATION"
            else:
                raise ValueError(
                    'mode parsed as boolean True — YAML 1.1 treats bare on/yes/true '
                    'as booleans; quote the value explicitly, e.g. mode: "LIVE"'
                )
        return data

    @model_validator(mode="after")
    def validate_caps(self) -> "AutotradePolicy":
        if self.max_dollar_risk_per_order > self.max_daily_dollar_risk:
            raise ValueError("per-order risk cannot exceed daily risk")
        return self


class IntentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    seq: int
    at: datetime
    event: str
    from_status: IntentStatus | None = None
    to_status: IntentStatus
    detail: str | None = None
    alert_id: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    module: str | None = None
    mode: AutomationMode | None = None
    receipt: dict[str, Any] | None = None
    trade_event: dict[str, Any] | None = None


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
    capital_check: dict[str, Any] | None = None
    option_selection: dict[str, Any] | None = None
    trade_events: list[dict[str, Any]] = Field(default_factory=list)
    events: list[IntentEvent] = Field(default_factory=list)
    revision: int = 1


class OptionCandidate(BaseModel):
    """One official-MCP option instrument plus its live quote."""

    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1)
    chain_symbol: str
    expiration_date: date
    option_type: Literal["call", "put"]
    strike_price: float = Field(gt=0)
    delta: float
    bid_price: float = Field(ge=0)
    ask_price: float = Field(gt=0)
    mark_price: float = Field(gt=0)
    volume: int = Field(ge=0)
    open_interest: int = Field(ge=0)
    quote_at: datetime
    sellout_at: datetime
    tradable: bool = True


class BrokerReceipt(BaseModel):
    """Allowlisted normalized fields copied from an official MCP result."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["EXECUTED", "REJECTED", "FAILED", "CANCELLED", "CLOSED"]
    reason: str = Field(min_length=1, max_length=500)
    observed_at: datetime
    broker_order_id: str | None = Field(default=None, max_length=200)
    option_id: str | None = Field(default=None, max_length=200)
    filled_quantity: int | None = Field(default=None, ge=1)
    average_price: float | None = Field(default=None, gt=0)
    pretrade_alerts: list[str] = Field(default_factory=list, max_length=100)


class SimulationReceipt(BaseModel):
    """Fresh official-MCP quote used for a hypothetical fill."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event: Literal["ENTRY", "EXIT"]
    reason: str = Field(min_length=1, max_length=500)
    option_id: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    bid_price: float = Field(ge=0)
    ask_price: float = Field(gt=0)
    underlying_price: float = Field(gt=0)
    volume: int = Field(ge=0)
    open_interest: int = Field(ge=0)


class AutotradeManager:
    """Persistent intent state with deterministic alert→intent mapping."""

    def __init__(
        self,
        policy_path: str | Path | None = None,
        state_path: str | Path | None = None,
        state_store: SettingsStore | None = None,
        symbol_gate: Callable[[str], str | None] | None = None,
    ) -> None:
        self.local_only = os.getenv("INTELLIDHAN_LOCAL_ONLY", "").lower() in {"1", "true", "yes"}
        self.policy_path = Path(policy_path or os.getenv("AUTOTRADE_POLICY_PATH", "config/autotrade.yaml"))
        self.state_path = Path(
            state_path or os.getenv("AUTOTRADE_STATE_PATH", "data/autotrade_state.json")
        )
        self.state_store = state_store
        self.symbol_gate = symbol_gate
        self._policy_migrated = False
        self.policy = self._load_policy()
        self.intents: dict[str, ExecutionIntent] = self._load_state()
        claims_migrated = self._revoke_untrusted_claims()
        if self._policy_migrated:
            self._persist_policy()
        if claims_migrated:
            self._persist_state()
        if self.local_only:
            self._disable_live_authority(
                "local research runtime only permits Simulation; placement authority revoked",
                now=datetime.now(timezone.utc),
            )

    def _normalize_loaded_policy(self, payload: Any) -> dict[str, Any]:
        """Move any pre-v2 execution policy to the safe Codex baseline.

        Existing deployments may have a persisted agent identity that no longer
        owns execution. The cutover is intentionally fail-closed: normalize the
        identity, disarm the policy, and require an operator to review and arm it
        again under the new agent token.
        """
        clean = dict(payload or {})
        if (
            clean.get("contract_version") != AUTOTRADE_CONTRACT_VERSION
            or clean.get("agent") != EXECUTION_AGENT
        ):
            clean["contract_version"] = AUTOTRADE_CONTRACT_VERSION
            clean["agent"] = EXECUTION_AGENT
            clean["mode"] = AutomationMode.SIMULATION.value
            clean["live_until"] = None
            clean.pop("armed_until", None)
            clean["revision"] = int(clean.get("revision") or 0) + 1
            clean["updated_at"] = datetime.now(timezone.utc)
            self._policy_migrated = True
        if self.local_only and (
            clean.get("mode") != AutomationMode.SIMULATION.value or clean.get("live_until")
        ):
            clean["mode"] = AutomationMode.SIMULATION.value
            clean["live_until"] = None
            clean["revision"] = int(clean.get("revision") or 0) + 1
            clean["updated_at"] = datetime.now(timezone.utc)
            self._policy_migrated = True
        return clean

    def _load_policy(self) -> AutotradePolicy:
        if self.state_store is not None:
            persisted = self.state_store.get_setting("autotrade_policy")
            if persisted:
                return AutotradePolicy.model_validate(
                    self._normalize_loaded_policy(persisted)
                )
        if not self.policy_path.exists():
            return AutotradePolicy()
        raw = yaml.safe_load(self.policy_path.read_text()) or {}
        return AutotradePolicy.model_validate(
            self._normalize_loaded_policy(raw.get("autotrade", raw))
        )

    @staticmethod
    def _normalize_loaded_intent(payload: dict[str, Any]) -> dict[str, Any]:
        """Read v1 snapshots without granting their pre-v2 placement authority."""
        clean = dict(payload)
        legacy_mode = str(clean.get("mode") or "").upper()
        if legacy_mode in {"OFF", "SHADOW"}:
            clean["mode"] = AutomationMode.SIMULATION.value
        elif legacy_mode in {"SUPERVISED", "ARMED"}:
            status = str(clean.get("status") or "")
            if status in {
                IntentStatus.CLAIMED.value,
                IntentStatus.EXECUTED.value,
                IntentStatus.CANCELLED.value,
                IntentStatus.CLOSED.value,
                IntentStatus.FAILED.value,
            }:
                # Keep the broker-truth receipt path for in-flight exposure.
                clean["mode"] = AutomationMode.LIVE.value
                if status == IntentStatus.CLAIMED.value:
                    claim = dict(clean.get("claim") or {})
                    claim["migration_receipt_only"] = True
                    clean["claim"] = claim
            else:
                clean["mode"] = AutomationMode.SIMULATION.value
                clean["status"] = IntentStatus.BLOCKED.value
                reasons = list(clean.get("reasons") or [])
                reason = "pre-v2 execution intent disabled during two-mode migration"
                if reason not in reasons:
                    reasons.append(reason)
                clean["reasons"] = reasons
        events = []
        for event in clean.get("events") or []:
            item = dict(event)
            if str(item.get("mode") or "").upper() in {
                "OFF", "SHADOW", "SUPERVISED", "ARMED"
            }:
                item["mode"] = clean.get("mode", AutomationMode.SIMULATION.value)
            events.append(item)
        clean["events"] = events
        return clean

    @staticmethod
    def _normalize_loaded_event(payload: dict[str, Any]) -> dict[str, Any]:
        clean = dict(payload)
        if str(clean.get("mode") or "").upper() in {
            "OFF", "SHADOW", "SUPERVISED", "ARMED"
        }:
            clean["mode"] = AutomationMode.SIMULATION.value
        return clean

    def _load_state(self) -> dict[str, ExecutionIntent]:
        if self.state_store is not None:
            persisted = self.state_store.get_setting("autotrade_intents")
            intents = (
                {
                    item["intent_id"]: ExecutionIntent.model_validate(
                        self._normalize_loaded_intent(item)
                    )
                    for item in persisted.get("intents", [])
                }
                if persisted else {}
            )
            durable_events = self.state_store.list_autotrade_events()
            if durable_events:
                by_intent: dict[str, list[IntentEvent]] = {}
                for row in durable_events:
                    intent_id = row.pop("intent_id")
                    by_intent.setdefault(intent_id, []).append(
                        IntentEvent.model_validate(self._normalize_loaded_event(row))
                    )
                for intent_id, events in by_intent.items():
                    if intent_id in intents:
                        intents[intent_id].events = events
                        durable_trades = [
                            event.trade_event for event in events if event.trade_event
                        ]
                        if durable_trades:
                            intents[intent_id].trade_events = durable_trades
                        durable_receipts = [
                            event.receipt for event in events if event.receipt
                        ]
                        if durable_receipts:
                            intents[intent_id].receipt = durable_receipts[-1]
            else:
                # One-time migration from the older embedded event list.
                for intent in intents.values():
                    for event in intent.events:
                        self._persist_event(intent, event)
            return intents
        if not self.state_path.exists():
            return {}
        raw = json.loads(self.state_path.read_text())
        return {
            item["intent_id"]: ExecutionIntent.model_validate(
                self._normalize_loaded_intent(item)
            )
            for item in raw.get("intents", [])
        }

    def _revoke_untrusted_claims(self) -> bool:
        """Preserve broker truth while preventing retired agents from placing."""
        changed = False
        for intent in self.intents.values():
            if intent.status != IntentStatus.CLAIMED:
                continue
            claim = intent.claim or {}
            if claim.pop("migration_receipt_only", False):
                intent.claim = claim
                changed = self._mutate_revoked_claim(
                    intent,
                    "pre-v2 claim is receipt-only; placement authority was revoked",
                ) or changed
                continue
            if claim.get("agent") == self.policy.agent or claim.get("revoked_at"):
                continue
            changed = self._mutate_revoked_claim(
                intent,
                "execution agent is no longer authorized; reconcile broker state",
            ) or changed
        return changed

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
            "contract_version": AUTOTRADE_CONTRACT_VERSION,
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

    def _persist_event(self, intent: ExecutionIntent, event: IntentEvent) -> None:
        if self.state_store is not None:
            payload = event.model_dump(mode="json")
            payload.update({
                "alert_id": intent.alert_id,
                "symbol": intent.symbol,
                "strategy": intent.strategy,
                "module": intent.module,
                "mode": intent.mode.value,
            })
            self.state_store.append_autotrade_event(
                intent.intent_id, payload
            )

    def effective_mode(self, now: datetime | None = None) -> AutomationMode:
        if self.local_only:
            return AutomationMode.SIMULATION
        now = now or datetime.now(timezone.utc)
        if self.policy.mode == AutomationMode.LIVE:
            if self.policy.live_until is None or self.policy.live_until <= now:
                return AutomationMode.SIMULATION
        return self.policy.mode

    def update_policy(self, updates: dict[str, Any]) -> AutotradePolicy:
        now = datetime.now(timezone.utc)
        updates = dict(updates)
        live_for_minutes = updates.pop("live_for_minutes", None)
        # Accept the old field only to migrate safely; it never re-enables live.
        updates.pop("arm_for_minutes", None)
        legacy_mode = str(updates.get("mode", "")).upper()
        if legacy_mode in {"OFF", "SHADOW"}:
            updates["mode"] = AutomationMode.SIMULATION.value
        elif legacy_mode in {"SUPERVISED", "ARMED"}:
            updates["mode"] = AutomationMode.SIMULATION.value
        base = self.policy.model_dump()
        base.update(updates)
        base["revision"] = self.policy.revision + 1
        base["updated_at"] = now
        requested_mode = AutomationMode(base["mode"])
        if requested_mode == AutomationMode.LIVE:
            if self.local_only:
                raise ValueError("This local research runtime only permits SIMULATION.")
            if live_for_minutes is None or not 1 <= int(live_for_minutes) <= 480:
                raise ValueError("LIVE mode requires live_for_minutes between 1 and 480")
            base["live_until"] = now + timedelta(minutes=int(live_for_minutes))
        else:
            base["live_until"] = None
        candidate = AutotradePolicy.model_validate(base)
        if candidate.mode == AutomationMode.LIVE:
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
        if candidate.mode == AutomationMode.SIMULATION:
            self._disable_live_authority(
                "execution mode switched to Simulation; placement authority revoked",
                now=now,
            )
        return self.policy

    def _disable_live_authority(self, reason: str, *, now: datetime) -> None:
        changed = False
        for intent in self.intents.values():
            if intent.mode != AutomationMode.LIVE:
                continue
            if intent.status in BLOCKABLE_STATUSES:
                self._mutate_blocked(intent, reason, now=now)
                changed = True
            elif intent.status == IntentStatus.CLAIMED:
                changed = self._mutate_revoked_claim(intent, reason, now=now) or changed
        if changed:
            self._persist_state()

    def on_alert(self, alert: Alert, now: datetime | None = None) -> ExecutionIntent | None:
        now = now or datetime.now(timezone.utc)
        intent_id = f"ati_{alert.alert_id}"
        if intent_id in self.intents:
            intent = self.intents[intent_id]
            allowed_statuses = (
                {"ACTIVE"}
                if intent.mode == AutomationMode.LIVE
                else {"ACTIVE", "SHADOW"}
            )
            if alert.status not in allowed_statuses:
                return self._block_intent(intent, f"alert status is {alert.status.lower()}")
            reason = self._symbol_gate_reason(intent.symbol)
            if reason:
                return self._block_intent(intent, reason)
            return intent
        mode = self.effective_mode(now)
        reasons = self._eligibility_reasons(
            alert, now, live=mode == AutomationMode.LIVE
        )
        if reasons:
            status = IntentStatus.BLOCKED
        elif mode == AutomationMode.SIMULATION:
            status = IntentStatus.SHADOW
        elif mode == AutomationMode.LIVE:
            status = IntentStatus.READY
        else:
            raise AssertionError(f"unsupported automation mode {mode}")
        created_event = IntentEvent(
            seq=1,
            at=now,
            event="INTENT_CREATED",
            to_status=status,
            detail="; ".join(reasons) if reasons else None,
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            strategy=alert.strategy,
            module=alert.module.value,
            mode=mode,
        )
        intent = ExecutionIntent(
            intent_id=intent_id,
            alert_id=alert.alert_id,
            created_at=now,
            updated_at=now,
            valid_until=alert.valid_until,
            mode=mode,
            status=status,
            reasons=reasons,
            symbol=alert.symbol,
            strategy=alert.strategy,
            module=alert.module.value,
            confidence=alert.confidence,
            dollar_risk=alert.dollar_risk,
            order_plan=self._order_plan(alert),
            events=[created_event],
        )
        self.intents[intent_id] = intent
        self._persist_event(intent, created_event)
        self._persist_state()
        return intent

    def _eligibility_reasons(
        self, alert: Alert, now: datetime, *, live: bool
    ) -> list[str]:
        reasons: list[str] = []
        policy = self.policy
        allowed_statuses = {"ACTIVE"} if live else {"ACTIVE", "SHADOW"}
        if alert.status not in allowed_statuses:
            reasons.append(f"alert status is {alert.status.lower()}")
        if live and alert.research_only:
            reasons.append("research-only strategy cannot trade live")
        if alert.valid_until <= now:
            reasons.append("alert expired")
        if policy.allowed_symbols and alert.symbol.upper() not in policy.allowed_symbols:
            reasons.append("symbol not allowlisted")
        if policy.allowed_strategies and alert.strategy.upper() not in policy.allowed_strategies:
            reasons.append("strategy not allowlisted")
        if policy.allowed_modules and alert.module.value.upper() not in policy.allowed_modules:
            reasons.append("module not allowlisted")
        # The unvalidated 9EMA monitor is deliberately capped below the live
        # confidence floor. Let it collect Simulation evidence without raising
        # that confidence or bypassing any of the other eligibility checks.
        simulation_research_monitor = (
            not live
            and alert.research_only
            and alert.strategy.upper() == "EMA9_MTF_0DTE"
            and alert.module.value == "0DTE"
        )
        if alert.confidence < policy.min_confidence and not simulation_research_monitor:
            reasons.append("confidence below automation minimum")
        if alert.dollar_risk > policy.max_dollar_risk_per_order:
            reasons.append("per-order risk cap exceeded")
        if alert.vehicle == Vehicle.OPTION and not policy.allow_options:
            reasons.append("options automation disabled")
        dynamic_scalp_option = (
            alert.module.value == "0DTE"
            and alert.strategy.upper() == "EMA9_MTF_0DTE"
        )
        supported_long_open = alert.action in {Action.EQUITY_BUY, Action.BTO} or (
            dynamic_scalp_option and alert.action == Action.EQUITY_SELL
        )
        if not supported_long_open:
            reasons.append("only long-opening orders are supported")
        if live and alert.vehicle == Vehicle.OPTION and not dynamic_scalp_option:
            reasons.append(
                "static option plans are not eligible for Live; dynamic attestation is required"
            )
        symbol_reason = self._symbol_gate_reason(alert.symbol)
        if symbol_reason:
            reasons.append(symbol_reason)
        calibration = CalibrationMap.load(alert.strategy)
        if live and policy.require_explicit_calibration:
            if not calibration.buckets:
                reasons.append("no calibration evidence on file")
            if calibration.meta.get("live_eligible") is not True:
                reasons.append("strategy is not explicitly live eligible")
        open_intents = [
            item for item in self.intents.values()
            if item.mode == AutomationMode.LIVE
            and item.status not in TERMINAL_STATUSES and item.valid_until > now
        ]
        if live and len(open_intents) >= policy.max_open_intents:
            reasons.append("open-intent cap reached")
        day = now.astimezone(ET).date()
        reserved = sum(
            item.dollar_risk for item in self.intents.values()
            if item.mode == AutomationMode.LIVE
            and item.created_at.astimezone(ET).date() == day
            and item.status in {
                IntentStatus.AWAITING_APPROVAL,
                IntentStatus.READY,
                IntentStatus.CLAIMED,
                IntentStatus.EXECUTED,
                IntentStatus.CLOSED,
            }
        )
        if live and reserved + alert.dollar_risk > policy.max_daily_dollar_risk:
            reasons.append("daily automation risk cap exceeded")
        return reasons

    def _order_plan(self, alert: Alert) -> dict[str, Any]:
        instrument: dict[str, Any]
        dynamic_scalp_option = (
            alert.module.value == "0DTE"
            and alert.strategy.upper() == "EMA9_MTF_0DTE"
        )
        if dynamic_scalp_option:
            direction = "call" if alert.stop_underlying < alert.underlying_price else "put"
            instrument = {
                "type": "OPTION_SELECTION_REQUIRED",
                "underlying": alert.symbol,
                "option_type": direction,
                "expiration_days": list(self.policy.option_expiry_days),
                "selection_rule": "HIGHEST_ABSOLUTE_DELTA_FEASIBLE",
            }
        elif alert.vehicle == Vehicle.EQUITY:
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
            "capital_policy": {
                "max_available_capital_fraction": (
                    self.policy.max_available_capital_fraction
                ),
                "capital_required": None if dynamic_scalp_option else alert.capital_required,
                "fresh_buying_power_required": True,
                "use_maximum_within_all_caps": dynamic_scalp_option,
                "premium_at_risk_counts_as_dollar_risk": dynamic_scalp_option,
            },
            "option_selection": ({
                "prefer": "highest absolute delta that fits at least one contract",
                "then_size": "maximum whole contracts inside every capital/risk cap",
                "price_basis": "current ask for conservative debit sizing",
                "min_volume": self.policy.min_option_volume,
                "min_open_interest": self.policy.min_option_open_interest,
                "max_spread_fraction": self.policy.max_option_spread_fraction,
                "max_quote_age_seconds": self.policy.max_option_quote_age_seconds,
            } if dynamic_scalp_option else None),
            "option_selection_request": (
                dict(instrument) if dynamic_scalp_option else None
            ),
            "protection": {
                "must_be_established": True,
                "stop_underlying": alert.stop_underlying,
                "stop_rule": alert.stop_rule,
                "take_profits": [tp.model_dump(mode="json") for tp in alert.take_profits],
                "management": (
                    [
                        "Hold the full position while the closed 5-minute bar remains on the trend side of its 9EMA",
                        "Move the risk reference to breakeven after +1R; do not cap upside with fixed profit targets",
                        "Exit the full position on a closed 5-minute 9EMA break, opposing 15-minute trend, hard stop, or broker sellout deadline",
                        "Never average down; flatten before the contract's authoritative sellout time",
                    ] if dynamic_scalp_option else alert.management
                ),
                "exit_style": (
                    "TREND_BREAK_FULL_EXIT" if dynamic_scalp_option else "PRINTED_PLAN"
                ),
            },
            "abort_if": [
                "MCP pre-trade review returns a blocking alert",
                "price is outside the entry zone",
                "protective exit cannot be established",
                "intent or alert has expired",
                "symbol market data is stale, unavailable, or quarantined",
                "Robinhood account is not the dedicated Agentic account",
                (
                    "required capital exceeds "
                    f"{self.policy.max_available_capital_fraction:.0%} of fresh "
                    "available buying power"
                ),
            ],
        }

    def attest_option_selection(
        self,
        intent_id: str,
        *,
        agent: str,
        buying_power: float,
        observed_at: datetime,
        account_scope: str,
        candidates: list[OptionCandidate | dict[str, Any]],
    ) -> ExecutionIntent:
        """Select the highest-delta liquid contract and maximum allowed size.

        Candidate data must come from the official Robinhood MCP. The app
        independently applies quote freshness, DTE, liquidity, direction, and
        every capital/risk ceiling so the agent cannot silently upsize.
        """
        if agent != self.policy.agent:
            raise ValueError(f"option selection agent must be {self.policy.agent}")
        if buying_power <= 0:
            raise ValueError("buying power must be positive")
        if account_scope != "ROBINHOOD_AGENTIC_ONLY":
            raise ValueError("option selection must target the dedicated Agentic account")
        now = datetime.now(timezone.utc)
        intent = self._get(intent_id)
        if intent.status not in {IntentStatus.SHADOW, IntentStatus.READY}:
            raise ValueError("option selection requires a SIMULATION or READY intent")
        if intent.valid_until <= now:
            raise ValueError("option selection cannot use an expired intent")
        if (
            intent.mode == AutomationMode.LIVE
            and self.effective_mode(now) != AutomationMode.LIVE
        ):
            raise ValueError("LIVE window expired; switch to Live again before selection")
        requested = (
            intent.order_plan.get("option_selection_request")
            or intent.order_plan.get("instrument")
            or {}
        )
        if requested.get("type") != "OPTION_SELECTION_REQUIRED":
            raise ValueError("intent does not require dynamic option selection")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("option selection timestamp must include a timezone")
        observed = observed_at.astimezone(timezone.utc)
        if not -5 <= (now - observed).total_seconds() <= CAPITAL_REVIEW_MAX_AGE_SECONDS:
            raise ValueError("option selection must use fresh buying power from the MCP")

        parsed = [
            item if isinstance(item, OptionCandidate) else OptionCandidate.model_validate(item)
            for item in candidates
        ]
        if not parsed:
            raise ValueError("at least one option candidate is required")
        if intent.mode == AutomationMode.SIMULATION:
            self._check_simulation_concurrency(intent.intent_id)
            daily_remaining = self._remaining_simulation_daily_risk(
                now, exclude_intent_id=intent.intent_id,
            )
        else:
            daily_remaining = self._remaining_daily_risk(
                now, exclude_intent_id=intent.intent_id,
            )
        capital_ceiling = round(
            min(
                buying_power * self.policy.max_available_capital_fraction,
                self.policy.max_dollar_risk_per_order,
                daily_remaining,
            ),
            2,
        )
        if capital_ceiling <= 0:
            raise ValueError("no capital remains inside the configured thresholds")
        wanted_type = str(requested["option_type"]).lower()
        today = observed.astimezone(ET).date()
        eligible: list[tuple[OptionCandidate, float, float]] = []
        rejection_counts: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        for item in parsed:
            if item.chain_symbol.upper() != intent.symbol.upper():
                reject("symbol")
                continue
            dte = (item.expiration_date - today).days
            if dte not in self.policy.option_expiry_days:
                reject("expiry")
                continue
            if item.option_type != wanted_type:
                reject("direction")
                continue
            if not item.tradable:
                reject("tradability")
                continue
            if item.sellout_at.tzinfo is None or item.sellout_at.utcoffset() is None:
                reject("sellout_timezone")
                continue
            if item.sellout_at.astimezone(timezone.utc) <= now + timedelta(minutes=5):
                reject("sellout_window")
                continue
            if item.quote_at.tzinfo is None or item.quote_at.utcoffset() is None:
                reject("quote_timezone")
                continue
            quote_age = (now - item.quote_at.astimezone(timezone.utc)).total_seconds()
            if quote_age < -5 or quote_age > self.policy.max_option_quote_age_seconds:
                reject("quote_age")
                continue
            if item.bid_price <= 0:
                reject("two_sided_market")
                continue
            mid = (item.bid_price + item.ask_price) / 2
            spread_fraction = (item.ask_price - item.bid_price) / mid
            if spread_fraction < 0 or spread_fraction > self.policy.max_option_spread_fraction:
                reject("spread")
                continue
            if item.volume < self.policy.min_option_volume:
                reject("volume")
                continue
            if item.open_interest < self.policy.min_option_open_interest:
                reject("open_interest")
                continue
            if not 0 < abs(item.delta) <= 1:
                reject("delta")
                continue
            if (item.option_type == "call" and item.delta <= 0) or (
                item.option_type == "put" and item.delta >= 0
            ):
                reject("delta_direction")
                continue
            contract_debit = round(item.ask_price * 100, 2)
            if contract_debit > capital_ceiling + 1e-9:
                reject("capital")
                continue
            eligible.append((item, spread_fraction, contract_debit))
        if not eligible:
            detail = ", ".join(f"{key}={value}" for key, value in sorted(rejection_counts.items()))
            raise ValueError(f"no liquid 0/1DTE option fits every threshold ({detail})")

        selected, spread_fraction, contract_debit = sorted(
            eligible,
            key=lambda row: (
                -abs(row[0].delta), row[1], -row[0].open_interest,
                row[0].ask_price, row[0].option_id,
            ),
        )[0]
        quantity = int(capital_ceiling // contract_debit)
        planned_debit = round(quantity * contract_debit, 2)
        selection = {
            **selected.model_dump(mode="json"),
            "quantity": quantity,
            "contract_debit_at_ask": contract_debit,
            "planned_debit": planned_debit,
            "capital_ceiling": capital_ceiling,
            "buying_power": round(buying_power, 2),
            "max_available_capital_fraction": self.policy.max_available_capital_fraction,
            "spread_fraction": round(spread_fraction, 6),
            "eligible_candidate_count": len(eligible),
            "submitted_candidate_count": len(parsed),
            "selection_rule": "HIGHEST_ABSOLUTE_DELTA_FEASIBLE",
            "observed_at": observed.isoformat(),
            "recorded_at": now.isoformat(),
            "rejection_counts": rejection_counts,
        }
        intent.option_selection = selection
        intent.order_plan["instrument"] = {
            "type": "OPTION",
            "underlying": intent.symbol,
            "option_id": selected.option_id,
            "option_type": selected.option_type,
            "expiration_date": selected.expiration_date.isoformat(),
            "strike_price": selected.strike_price,
            "delta": selected.delta,
            "contracts": quantity,
        }
        intent.order_plan["entry"].update({
            "order_type": "LIMIT",
            "option_limit_price": selected.ask_price,
            "price_basis": "fresh ask; broker review required",
            "time_in_force": "GFD",
        })
        intent.order_plan["capital_policy"]["capital_required"] = planned_debit
        intent.order_plan["capital_policy"]["capital_ceiling"] = capital_ceiling
        intent.capital_check = None
        intent.updated_at = now
        intent.revision += 1
        self._append_event(
            intent,
            "OPTION_SELECTED",
            intent.status,
            intent.status,
            (
                f"{selected.expiration_date} {selected.strike_price:g} "
                f"{selected.option_type.upper()} delta {selected.delta:+.3f}; "
                f"{quantity} contract(s), ${planned_debit:.2f} debit"
            ),
            at=now,
        )
        self._persist_state()
        return intent

    def _remaining_daily_risk(
        self, now: datetime, *, exclude_intent_id: str | None = None
    ) -> float:
        used = sum(
            float((item.option_selection or {}).get("planned_debit") or item.dollar_risk)
            for item in self.intents.values()
            if item.intent_id != exclude_intent_id
            and item.mode == AutomationMode.LIVE
            and item.created_at.astimezone(ET).date() == now.astimezone(ET).date()
            and item.status in {
                IntentStatus.READY,
                IntentStatus.CLAIMED,
                IntentStatus.EXECUTED,
                IntentStatus.CLOSED,
            }
        )
        return max(0.0, self.policy.max_daily_dollar_risk - used)

    def _check_simulation_concurrency(self, exclude_intent_id: str) -> None:
        # Unfilled SHADOW plans are research candidates, not capital exposure.
        opened = sum(
            item.intent_id != exclude_intent_id
            and item.mode == AutomationMode.SIMULATION
            and item.status == IntentStatus.EXECUTED
            for item in self.intents.values()
        )
        if opened >= self.policy.max_open_intents:
            raise ValueError("simulation open-position cap reached")

    def _remaining_simulation_daily_risk(
        self, now: datetime, *, exclude_intent_id: str | None = None,
    ) -> float:
        """Reserve actual simulated entry debit for its entry day, even after exit.

        Simulation observations never consume or release Live risk capacity.
        Missing entry evidence on an imported filled intent fails closed rather
        than assigning zero risk to unknown historical exposure.
        """
        used = 0.0
        for item in self.intents.values():
            if item.intent_id == exclude_intent_id or item.mode != AutomationMode.SIMULATION:
                continue
            entries = [row for row in item.trade_events if row.get("event") == "ENTRY"]
            if not entries and item.status in {IntentStatus.EXECUTED, IntentStatus.CLOSED}:
                raise ValueError("simulation entry history is missing; verify the trade journal")
            for entry in entries:
                try:
                    observed = datetime.fromisoformat(str(entry["observed_at"]))
                    debit = float(entry["option_price"]) * int(entry["quantity"]) * 100
                    if observed.tzinfo is None or not math.isfinite(debit) or debit <= 0:
                        raise ValueError
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("simulation entry history is invalid; verify the trade journal") from exc
                if observed.astimezone(ET).date() == now.astimezone(ET).date():
                    used += round(debit, 2)
        return max(0.0, self.policy.max_daily_dollar_risk - used)

    def _check_simulation_entry_capital(
        self, intent: ExecutionIntent, ask_price: float, now: datetime,
    ) -> None:
        self._check_simulation_concurrency(intent.intent_id)
        selected = intent.option_selection or {}
        try:
            quantity = selected["quantity"]
            buying_power = float(selected["buying_power"])
            selected_ceiling = float(selected["capital_ceiling"])
            observed = datetime.fromisoformat(str(selected["observed_at"]))
            if type(quantity) is not int or quantity <= 0 or observed.tzinfo is None:
                raise ValueError
            if not all(math.isfinite(value) and value > 0 for value in (buying_power, selected_ceiling, ask_price)):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("simulation capital evidence is invalid; select again before entry") from exc
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        if not -5 <= age <= CAPITAL_REVIEW_MAX_AGE_SECONDS:
            raise ValueError("simulation buying-power observation is stale; select again before entry")
        actual_debit = round(ask_price * quantity * 100, 2)
        ceiling = min(
            selected_ceiling,
            buying_power * self.policy.max_available_capital_fraction,
            self.policy.max_dollar_risk_per_order,
            self._remaining_simulation_daily_risk(now, exclude_intent_id=intent.intent_id),
        )
        if not math.isfinite(actual_debit) or actual_debit > ceiling + 1e-9:
            raise ValueError("simulation entry debit exceeds the current capital or daily risk cap; select again before entry")

    def approve(self, intent_id: str) -> ExecutionIntent:
        """Retained only for imported v1 intents awaiting approval."""
        intent = self._get(intent_id)
        if intent.status != IntentStatus.AWAITING_APPROVAL:
            raise ValueError("only AWAITING_APPROVAL intents can be approved")
        if intent.valid_until <= datetime.now(timezone.utc):
            return self._transition(intent, IntentStatus.EXPIRED)
        reason = self._symbol_gate_reason(intent.symbol)
        if reason:
            return self._block_intent(intent, reason)
        return self._transition(
            intent, IntentStatus.READY, event="OPERATOR_APPROVED"
        )

    def reject(self, intent_id: str, reason: str) -> ExecutionIntent:
        intent = self._get(intent_id)
        detail = reason or "rejected by operator"
        if intent.status == IntentStatus.CLAIMED:
            return self._revoke_claim(intent, detail)
        if intent.status not in BLOCKABLE_STATUSES:
            raise ValueError("only pre-execution intents can be rejected")
        intent.reasons.append(detail)
        return self._transition(
            intent, IntentStatus.REJECTED, event="OPERATOR_REJECTED", detail=detail
        )

    def attest_capital(
        self,
        intent_id: str,
        *,
        agent: str,
        buying_power: float,
        observed_at: datetime,
        currency: str,
        account_scope: str,
    ) -> ExecutionIntent:
        """Machine-check a fresh MCP buying-power observation before claim."""
        if agent != self.policy.agent:
            raise ValueError(f"capital review agent must be {self.policy.agent}")
        intent = self._get(intent_id)
        now = datetime.now(timezone.utc)
        if intent.status == IntentStatus.CLAIMED:
            if (intent.claim or {}).get("revoked_at"):
                raise ValueError("intent claim was revoked; a fresh alert is required")
            lease = (intent.claim or {}).get("lease_until")
            if lease and datetime.fromisoformat(lease) > now:
                raise ValueError("capital review cannot replace an active claim lease")
        elif intent.status != IntentStatus.READY:
            raise ValueError("capital review requires a READY or expired-lease intent")
        if intent.mode != AutomationMode.LIVE:
            raise ValueError("capital review is only used for LIVE intents")
        if (
            intent.order_plan.get("instrument", {}).get("type")
            == "OPTION_SELECTION_REQUIRED"
        ):
            raise ValueError("fresh option selection is required before capital review")
        if buying_power <= 0:
            raise ValueError("buying power must be positive")
        if currency != "USD":
            raise ValueError("capital review currency must be USD")
        if account_scope != "ROBINHOOD_AGENTIC_ONLY":
            raise ValueError("capital review must target the dedicated Agentic account")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("capital review timestamp must include a timezone")
        observed = observed_at.astimezone(timezone.utc)
        age = (now - observed).total_seconds()
        if age < -5 or age > CAPITAL_REVIEW_MAX_AGE_SECONDS:
            raise ValueError("capital review must use fresh buying power from the MCP")
        required = float(intent.order_plan["capital_policy"]["capital_required"])
        fraction = self.policy.max_available_capital_fraction
        maximum = round(float(buying_power) * fraction, 2)
        passed = required <= maximum + 1e-9
        intent.capital_check = {
            "agent": agent,
            "account_scope": account_scope,
            "currency": currency,
            "buying_power": round(float(buying_power), 2),
            "observed_at": observed.isoformat(),
            "recorded_at": now.isoformat(),
            "max_available_capital_fraction": fraction,
            "max_capital": maximum,
            "capital_required": required,
            "passed": passed,
        }
        self._append_event(
            intent,
            "CAPITAL_PREFLIGHT",
            intent.status,
            intent.status,
            f"required ${required:.2f}; ceiling ${maximum:.2f}; passed={passed}",
            at=now,
        )
        intent.updated_at = now
        intent.revision += 1
        if not passed:
            return self._block_intent(
                intent,
                f"required capital ${required:.2f} exceeds "
                f"{fraction:.0%} buying-power ceiling ${maximum:.2f}",
            )
        self._persist_state()
        return intent

    def claim(
        self,
        intent_id: str,
        agent: str,
        *,
        underlying_price: float,
        observed_at: datetime,
    ) -> ExecutionIntent:
        if agent != self.policy.agent:
            raise ValueError(f"claim agent must be {self.policy.agent}")
        intent = self._get(intent_id)
        if intent.mode != AutomationMode.LIVE:
            raise ValueError("SIMULATION intents cannot be claimed for broker execution")
        now = datetime.now(timezone.utc)
        if self.effective_mode(now) != AutomationMode.LIVE:
            return self._block_intent(intent, "LIVE window expired or switched to Simulation")
        if intent.status not in {IntentStatus.READY, IntentStatus.CLAIMED}:
            raise ValueError("only READY intents can be claimed")
        if intent.valid_until <= now:
            if intent.status == IntentStatus.CLAIMED:
                return self._revoke_claim(
                    intent, "intent expired while broker outcome was pending"
                )
            return self._transition(intent, IntentStatus.EXPIRED)
        reason = self._symbol_gate_reason(intent.symbol)
        if reason:
            return self._block_intent(intent, reason)
        policy_reason = self._current_claim_policy_reason(intent, now)
        if policy_reason:
            return self._block_intent(intent, policy_reason)
        if underlying_price <= 0:
            raise ValueError("positive underlying price is required before claim")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("underlying quote timestamp must include a timezone")
        underlying_age = (
            now - observed_at.astimezone(timezone.utc)
        ).total_seconds()
        if underlying_age < -5 or underlying_age > self.policy.max_option_quote_age_seconds:
            raise ValueError("underlying quote is stale")
        try:
            zone_low, zone_high = intent.order_plan["entry"]["entry_zone"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("underlying entry zone is missing or invalid") from exc
        if not float(zone_low) <= underlying_price <= float(zone_high):
            raise ValueError("underlying price is outside the approved entry zone")
        if intent.status == IntentStatus.CLAIMED:
            if (intent.claim or {}).get("revoked_at"):
                raise ValueError("intent claim was revoked; a fresh alert is required")
            lease = (intent.claim or {}).get("lease_until")
            if lease and datetime.fromisoformat(lease) > now:
                raise ValueError("intent already has an active claim")
        capital_reason = self._capital_check_reason(intent, now)
        if capital_reason:
            raise ValueError(capital_reason)
        intent.claim = {
            "agent": agent,
            "claimed_at": now.isoformat(),
            "lease_until": min(
                now + timedelta(minutes=2),
                intent.valid_until,
                self.policy.live_until or intent.valid_until,
            ).isoformat(),
            "underlying_price": round(float(underlying_price), 6),
            "underlying_observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        }
        return self._transition(
            intent, IntentStatus.CLAIMED, event="CLAIM_ACQUIRED",
            detail=f"claimed by {agent} with a two-minute lease",
        )

    def _current_claim_policy_reason(
        self, intent: ExecutionIntent, now: datetime
    ) -> str | None:
        policy = self.policy
        if intent.symbol.upper() not in policy.allowed_symbols:
            return "symbol is no longer allowlisted"
        if intent.strategy.upper() not in policy.allowed_strategies:
            return "strategy is no longer allowlisted"
        if intent.module.upper() not in policy.allowed_modules:
            return "module is no longer allowlisted"
        if intent.confidence < policy.min_confidence:
            return "confidence is below the current automation minimum"
        instrument_type = str(
            (intent.order_plan.get("instrument") or {}).get("type") or ""
        )
        uses_options = instrument_type in {
            "OPTION",
            "OPTION_SELECTION_REQUIRED",
        } or intent.option_selection is not None
        if uses_options and not policy.allow_options:
            return "options automation is currently disabled"
        if instrument_type == "OPTION" and intent.option_selection is None:
            return "static option plans cannot be claimed without dynamic attestation"
        if policy.require_explicit_calibration:
            calibration = CalibrationMap.load(intent.strategy)
            if not calibration.buckets:
                return "no current calibration evidence is on file"
            if calibration.meta.get("live_eligible") is not True:
                return "strategy is no longer explicitly live eligible"
        open_others = [
            item
            for item in self.intents.values()
            if item.intent_id != intent.intent_id
            and item.mode == AutomationMode.LIVE
            and item.status not in TERMINAL_STATUSES
            and item.valid_until > now
        ]
        if len(open_others) >= policy.max_open_intents:
            return "current open-intent cap is reached"
        return None

    def _capital_check_reason(
        self, intent: ExecutionIntent, now: datetime
    ) -> str | None:
        try:
            required = float(intent.order_plan["capital_policy"]["capital_required"])
        except (KeyError, TypeError, ValueError):
            return "capital required is missing or invalid"
        if required <= 0:
            return "capital required must be positive"
        dynamic_scalp_option = (
            intent.strategy.upper() == "EMA9_MTF_0DTE"
            and intent.module.upper() == "0DTE"
        )
        risk_amount = required if dynamic_scalp_option else float(intent.dollar_risk)
        if risk_amount > self.policy.max_dollar_risk_per_order + 1e-9:
            return "order risk exceeds the current per-order risk cap"
        if risk_amount > self._remaining_daily_risk(
            now, exclude_intent_id=intent.intent_id
        ) + 1e-9:
            return "order risk exceeds the remaining daily risk cap"

        if dynamic_scalp_option:
            selection = intent.option_selection
            if not selection:
                return "fresh option selection is required before claim"
            try:
                quote_at = datetime.fromisoformat(str(selection["quote_at"]))
                sellout_at = datetime.fromisoformat(str(selection["sellout_at"]))
                planned_debit = float(selection["planned_debit"])
            except (KeyError, TypeError, ValueError):
                return "option selection is invalid"
            if quote_at.tzinfo is None or quote_at.utcoffset() is None:
                return "option selection quote timestamp is invalid"
            quote_age = (now - quote_at.astimezone(timezone.utc)).total_seconds()
            if quote_age < -5 or quote_age > self.policy.max_option_quote_age_seconds:
                return "option selection quote is stale"
            if sellout_at.tzinfo is None or sellout_at.utcoffset() is None:
                return "option sellout timestamp is invalid"
            if sellout_at.astimezone(timezone.utc) <= now + timedelta(minutes=5):
                return "option is inside the broker sellout window"
            if abs(planned_debit - required) > 1e-9:
                return "option selection debit does not match the order plan"
            try:
                expiration_date = date.fromisoformat(str(selection["expiration_date"]))
                spread_fraction = float(selection["spread_fraction"])
                volume = int(selection["volume"])
                open_interest = int(selection["open_interest"])
            except (KeyError, TypeError, ValueError):
                return "option selection policy evidence is invalid"
            dte = (expiration_date - now.astimezone(ET).date()).days
            if dte not in self.policy.option_expiry_days:
                return "selected option expiry is no longer allowed"
            if spread_fraction > self.policy.max_option_spread_fraction:
                return "selected option spread exceeds the current cap"
            if volume < self.policy.min_option_volume:
                return "selected option volume is below the current floor"
            if open_interest < self.policy.min_option_open_interest:
                return "selected option open interest is below the current floor"

        check = intent.capital_check
        if not check:
            return "fresh Robinhood buying-power review is required before claim"
        if check.get("passed") is not True:
            return "Robinhood buying-power review did not pass"
        try:
            observed = datetime.fromisoformat(str(check["observed_at"]))
        except (KeyError, TypeError, ValueError):
            return "Robinhood buying-power review timestamp is invalid"
        if observed.tzinfo is None or observed.utcoffset() is None:
            return "Robinhood buying-power review timestamp is invalid"
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > CAPITAL_REVIEW_MAX_AGE_SECONDS:
            return "Robinhood buying-power review is stale"
        try:
            buying_power = float(check["buying_power"])
            checked_required = float(check["capital_required"])
        except (KeyError, TypeError, ValueError):
            return "Robinhood buying-power review values are invalid"
        if abs(checked_required - required) > 1e-9:
            return "Robinhood buying-power review does not match the current order"
        current_maximum = buying_power * self.policy.max_available_capital_fraction
        if required > current_maximum + 1e-9:
            return "planned debit exceeds the current buying-power threshold"
        return None

    def block_symbol(self, symbol: str, reason: str) -> list[ExecutionIntent]:
        """Block unplaced work without overwriting in-flight broker truth."""
        changed: list[ExecutionIntent] = []
        for intent in self.intents.values():
            if intent.symbol.upper() != symbol.upper():
                continue
            if intent.status in BLOCKABLE_STATUSES:
                self._mutate_blocked(intent, reason)
                changed.append(intent)
            elif intent.status == IntentStatus.CLAIMED:
                if self._mutate_revoked_claim(intent, reason):
                    changed.append(intent)
        if changed:
            self._persist_state()
        return changed

    def record_receipt(self, intent_id: str, payload: dict[str, Any]) -> ExecutionIntent:
        intent = self._get(intent_id)
        if intent.mode != AutomationMode.LIVE:
            raise ValueError("broker receipts are only accepted for LIVE intents")
        try:
            parsed = BrokerReceipt.model_validate(payload)
            status = IntentStatus(parsed.status)
        except (KeyError, ValueError) as exc:
            raise ValueError("receipt fields are invalid or not allowlisted") from exc
        allowed = {
            IntentStatus.CLAIMED: {
                IntentStatus.EXECUTED,
                IntentStatus.REJECTED,
                IntentStatus.FAILED,
                IntentStatus.CANCELLED,
            },
            # A broker may report a late fill after acknowledging cancellation.
            # Exposure truth must win over the earlier local terminal state.
            IntentStatus.CANCELLED: {IntentStatus.EXECUTED},
            IntentStatus.EXECUTED: {IntentStatus.CLOSED, IntentStatus.FAILED},
        }
        if status not in allowed.get(intent.status, set()):
            raise ValueError(f"invalid receipt transition {intent.status.value} → {status.value}")
        now = datetime.now(timezone.utc)
        observed = parsed.observed_at
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("broker receipt timestamp must include a timezone")
        observed_utc = observed.astimezone(timezone.utc)
        if observed_utc > now + timedelta(seconds=5):
            raise ValueError("broker receipt timestamp cannot be in the future")

        receipt = parsed.model_dump(mode="json")
        receipt["observed_at"] = observed_utc.isoformat()
        receipt["recorded_at"] = now.isoformat()
        selection = intent.option_selection
        if status in {IntentStatus.EXECUTED, IntentStatus.CLOSED}:
            if not parsed.broker_order_id:
                raise ValueError("filled broker receipts require broker_order_id")
            if parsed.average_price is None or parsed.filled_quantity is None:
                raise ValueError("filled broker receipts require price and quantity")
            if parsed.pretrade_alerts:
                raise ValueError("broker pre-trade alerts block order recording")
            if selection:
                selected_id = str(selection["option_id"])
                if parsed.option_id and parsed.option_id != selected_id:
                    raise ValueError("broker receipt option does not match selection")
                receipt["option_id"] = selected_id
                planned_quantity = int(selection["quantity"])
                if status == IntentStatus.EXECUTED:
                    if parsed.filled_quantity > planned_quantity:
                        raise ValueError("broker fill exceeds selected quantity")
                else:
                    entry = next(
                        (row for row in intent.trade_events if row.get("event") == "ENTRY"),
                        None,
                    )
                    if entry is None:
                        raise ValueError("broker exit has no recorded entry")
                    if parsed.filled_quantity != int(entry["quantity"]):
                        raise ValueError("broker exit must close the full recorded quantity")
        intent.receipt = receipt
        trade_event = self._trade_event(intent, receipt, simulated=False)
        if status == IntentStatus.CLOSED:
            entry = next(
                row for row in intent.trade_events if row.get("event") == "ENTRY"
            )
            quantity = int(entry["quantity"])
            trade_event["realized_pnl"] = round(
                (float(trade_event["option_price"]) - float(entry["option_price"]))
                * 100
                * quantity,
                2,
            )
            trade_event["return_pct"] = round(
                (float(trade_event["option_price"]) / float(entry["option_price"]) - 1)
                * 100,
                4,
            )
        intent.trade_events.append(trade_event)
        broker_id = receipt.get("broker_order_id")
        detail = f"broker order {broker_id}" if broker_id else "broker receipt recorded"
        target_status = status
        event_name = "BROKER_RECEIPT"
        if intent.status == IntentStatus.EXECUTED and status == IntentStatus.FAILED:
            # A failed exit/protection action is not proof that exposure closed.
            # Keep the position open and auditable until broker truth says CLOSED.
            target_status = IntentStatus.EXECUTED
            event_name = "BROKER_EXIT_FAILED"
        return self._transition(
            intent,
            target_status,
            event=event_name,
            detail=detail,
            receipt=receipt,
            trade_event=trade_event,
        )

    def record_simulation(self, intent_id: str, payload: dict[str, Any]) -> ExecutionIntent:
        """Record an option entry/exit using real observed quotes, without orders."""
        intent = self._get(intent_id)
        if intent.mode != AutomationMode.SIMULATION:
            raise ValueError("simulation receipts require a SIMULATION intent")
        try:
            parsed = SimulationReceipt.model_validate(payload)
        except ValueError as exc:
            raise ValueError("simulation receipt fields are invalid") from exc
        event = parsed.event
        expected = IntentStatus.SHADOW if event == "ENTRY" else IntentStatus.EXECUTED
        if intent.status != expected:
            raise ValueError(
                f"simulation {event} requires {expected.value} status"
            )
        if intent.option_selection is None:
            raise ValueError("option selection is required before simulation entry")
        now = datetime.now(timezone.utc)
        if event == "ENTRY" and intent.valid_until <= now:
            raise ValueError("simulation entry cannot use an expired intent")
        symbol_reason = self._symbol_gate_reason(intent.symbol)
        if event == "ENTRY" and symbol_reason:
            raise ValueError(symbol_reason)
        selected_option_id = str(intent.option_selection["option_id"])
        if parsed.option_id != selected_option_id:
            raise ValueError("simulation quote option does not match selection")
        observed = parsed.observed_at
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        observed_utc = observed.astimezone(timezone.utc)
        quote_age = (now - observed_utc).total_seconds()
        if quote_age < -5 or quote_age > self.policy.max_option_quote_age_seconds:
            raise ValueError("simulation quote is stale")
        if parsed.ask_price < parsed.bid_price:
            raise ValueError("simulation quote has an inverted market")
        mid = (parsed.bid_price + parsed.ask_price) / 2
        spread_fraction = (parsed.ask_price - parsed.bid_price) / mid if mid else 1.0
        if event == "ENTRY":
            try:
                selected_at = datetime.fromisoformat(
                    str(intent.option_selection["recorded_at"])
                )
                sellout_at = datetime.fromisoformat(
                    str(intent.option_selection["sellout_at"])
                )
                zone_low, zone_high = intent.order_plan["entry"]["entry_zone"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("option selection or entry zone is invalid") from exc
            if selected_at.tzinfo is None or selected_at.utcoffset() is None:
                raise ValueError("option selection timestamp is invalid")
            selection_age = (now - selected_at.astimezone(timezone.utc)).total_seconds()
            if selection_age < -5 or selection_age > self.policy.max_option_quote_age_seconds:
                raise ValueError("option selection is stale; select again before entry")
            if sellout_at.astimezone(timezone.utc) <= now + timedelta(minutes=5):
                raise ValueError("option is inside the broker sellout window")
            if parsed.bid_price <= 0:
                raise ValueError("simulation entry requires a two-sided market")
            if spread_fraction > self.policy.max_option_spread_fraction:
                raise ValueError("simulation entry spread exceeds the liquidity cap")
            if parsed.volume < self.policy.min_option_volume:
                raise ValueError("simulation entry volume is below the liquidity floor")
            if parsed.open_interest < self.policy.min_option_open_interest:
                raise ValueError("simulation entry open interest is below the liquidity floor")
            if not float(zone_low) <= parsed.underlying_price <= float(zone_high):
                raise ValueError("underlying price is outside the approved entry zone")
            self._check_simulation_entry_capital(intent, parsed.ask_price, now)
        option_price = parsed.ask_price if event == "ENTRY" else parsed.bid_price
        clean = {
            "event": event,
            "reason": parsed.reason,
            "observed_at": observed_utc.isoformat(),
            "recorded_at": now.isoformat(),
            "option_price": option_price,
            "bid_price": parsed.bid_price,
            "ask_price": parsed.ask_price,
            "spread_fraction": round(spread_fraction, 6),
            "volume": parsed.volume,
            "open_interest": parsed.open_interest,
            "underlying_price": parsed.underlying_price,
            "option_id": selected_option_id,
            "quantity": intent.option_selection["quantity"],
            "simulated": True,
        }
        if symbol_reason:
            clean["data_quality_reason"] = symbol_reason
        if event == "EXIT":
            entry = next(
                (row for row in intent.trade_events if row.get("event") == "ENTRY"),
                None,
            )
            if entry is None:
                raise ValueError("simulation exit has no recorded entry")
            quantity = int(entry["quantity"])
            clean["realized_pnl"] = round(
                (option_price - float(entry["option_price"])) * 100 * quantity, 2
            )
            clean["return_pct"] = round(
                (option_price / float(entry["option_price"]) - 1) * 100, 4
            )
        intent.trade_events.append(clean)
        intent.receipt = clean
        status = IntentStatus.EXECUTED if event == "ENTRY" else IntentStatus.CLOSED
        return self._transition(
            intent,
            status,
            event=f"SIMULATION_{event}",
            detail=parsed.reason,
            receipt=clean,
            trade_event=clean,
        )

    @staticmethod
    def _trade_event(
        intent: ExecutionIntent, receipt: dict[str, Any], *, simulated: bool
    ) -> dict[str, Any]:
        status = str(receipt.get("status") or "")
        event = "ENTRY" if status == IntentStatus.EXECUTED.value else (
            "EXIT" if status == IntentStatus.CLOSED.value else status
        )
        return {
            **receipt,
            "event": event,
            "reason": str(receipt.get("reason") or "broker execution receipt"),
            "option_price": receipt.get("average_price"),
            "quantity": receipt.get("filled_quantity"),
            "simulated": simulated,
            "intent_id": intent.intent_id,
            "symbol": intent.symbol,
            "strategy": intent.strategy,
        }

    def list_intents(self, status: str | None = None) -> list[ExecutionIntent]:
        self._expire_stale()
        items = sorted(self.intents.values(), key=lambda x: x.created_at, reverse=True)
        if status:
            wanted = IntentStatus(status)
            items = [item for item in items if item.status == wanted]
        return items

    def audit_log(self, limit: int = 500) -> list[dict[str, Any]]:
        """Flatten the append-only intent history for operator review/export."""
        rows: list[dict[str, Any]] = []
        if self.state_store is not None:
            durable = self.state_store.list_autotrade_events()
            event_pairs = [
                (
                    row["intent_id"],
                    self.intents.get(row["intent_id"]),
                    IntentEvent.model_validate({
                        key: value for key, value in row.items() if key != "intent_id"
                    }),
                )
                for row in durable
            ]
        else:
            event_pairs = [
                (intent.intent_id, intent, event)
                for intent in self.intents.values()
                for event in (
                    intent.events or [IntentEvent(
                        seq=1,
                        at=intent.created_at,
                        event="LEGACY_INTENT_IMPORTED",
                        to_status=intent.status,
                    )]
                )
            ]
        for intent_id, intent, event in event_pairs:
            rows.append({
                **event.model_dump(mode="json"),
                "intent_id": intent_id,
                "alert_id": event.alert_id or (intent.alert_id if intent else None),
                "symbol": event.symbol or (intent.symbol if intent else None),
                "strategy": event.strategy or (intent.strategy if intent else None),
                "module": event.module or (intent.module if intent else None),
                "mode": (
                    event.mode.value if event.mode
                    else intent.mode.value if intent
                    else None
                ),
            })
        rows.sort(
            key=lambda item: (
                item["at"], item["intent_id"], item["seq"], item["event_id"]
            ),
            reverse=True,
        )
        return rows[:max(1, min(limit, 5000))]

    def status(self) -> dict[str, Any]:
        items = self.list_intents()
        counts: dict[str, int] = {}
        for item in items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return {
            "contract_version": AUTOTRADE_CONTRACT_VERSION,
            "effective_mode": self.effective_mode().value,
            "local_only": self.local_only,
            "policy": self.policy.model_dump(mode="json"),
            "counts": counts,
            "ready": counts.get(IntentStatus.READY.value, 0),
            "simulating": counts.get(IntentStatus.SHADOW.value, 0),
            "open_trades": counts.get(IntentStatus.EXECUTED.value, 0),
            "agent": self.policy.agent,
            "broker": "Robinhood Trading MCP",
            "credentials_in_app": False,
        }

    def _expire_stale(self) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for intent in self.intents.values():
            if intent.status in BLOCKABLE_STATUSES and intent.valid_until <= now:
                previous = intent.status
                intent.status = IntentStatus.EXPIRED
                intent.updated_at = now
                intent.revision += 1
                self._append_event(
                    intent, "INTENT_EXPIRED", previous, IntentStatus.EXPIRED,
                    "validity window elapsed",
                )
                changed = True
            elif intent.status == IntentStatus.CLAIMED and intent.valid_until <= now:
                if not (intent.claim or {}).get("revoked_at"):
                    self._mutate_revoked_claim(
                        intent, "intent expired while broker outcome was pending", now=now
                    )
                    changed = True
        if (
            self.policy.mode == AutomationMode.LIVE
            and self.effective_mode(now) == AutomationMode.SIMULATION
        ):
            if changed:
                self._persist_state()
            self._disable_live_authority(
                "LIVE window expired; placement authority revoked", now=now
            )
            return
        if changed:
            self._persist_state()

    def _get(self, intent_id: str) -> ExecutionIntent:
        try:
            return self.intents[intent_id]
        except KeyError as exc:
            raise KeyError(f"unknown intent {intent_id}") from exc

    def _symbol_gate_reason(self, symbol: str) -> str | None:
        if self.symbol_gate is None:
            return None
        try:
            return self.symbol_gate(symbol)
        except Exception:
            return "symbol data safety check is unavailable"

    def _block_intent(self, intent: ExecutionIntent, reason: str) -> ExecutionIntent:
        if intent.status in BLOCKABLE_STATUSES:
            self._mutate_blocked(intent, reason)
            self._persist_state()
        elif intent.status == IntentStatus.CLAIMED:
            if self._mutate_revoked_claim(intent, reason):
                self._persist_state()
        return intent

    def _mutate_blocked(
        self, intent: ExecutionIntent, reason: str, *, now: datetime | None = None
    ) -> None:
        if reason not in intent.reasons:
            intent.reasons.append(reason)
        previous = intent.status
        intent.status = IntentStatus.BLOCKED
        intent.updated_at = now or datetime.now(timezone.utc)
        intent.revision += 1
        self._append_event(
            intent, "INTENT_BLOCKED", previous, IntentStatus.BLOCKED, reason
        )

    def _mutate_revoked_claim(
        self,
        intent: ExecutionIntent,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Revoke placement authority while retaining a late-receipt path."""
        if (intent.claim or {}).get("revoked_at"):
            return False
        if reason not in intent.reasons:
            intent.reasons.append(reason)
        revoked_at = now or datetime.now(timezone.utc)
        claim = dict(intent.claim or {})
        claim.setdefault("revoked_at", revoked_at.isoformat())
        claim.setdefault("revoked_reason", reason)
        claim["cancel_requested"] = True
        intent.claim = claim
        intent.updated_at = revoked_at
        intent.revision += 1
        self._append_event(
            intent, "CLAIM_REVOKED", intent.status, intent.status, reason,
            at=revoked_at,
        )
        return True

    def _revoke_claim(self, intent: ExecutionIntent, reason: str) -> ExecutionIntent:
        if self._mutate_revoked_claim(intent, reason):
            self._persist_state()
        return intent

    def _append_event(
        self,
        intent: ExecutionIntent,
        event: str,
        previous: IntentStatus | None,
        status: IntentStatus,
        detail: str | None = None,
        *,
        at: datetime | None = None,
        receipt: dict[str, Any] | None = None,
        trade_event: dict[str, Any] | None = None,
    ) -> None:
        item = IntentEvent(
            seq=len(intent.events) + 1,
            at=at or datetime.now(timezone.utc),
            event=event,
            from_status=previous,
            to_status=status,
            detail=detail,
            alert_id=intent.alert_id,
            symbol=intent.symbol,
            strategy=intent.strategy,
            module=intent.module,
            mode=intent.mode,
            receipt=receipt,
            trade_event=trade_event,
        )
        intent.events.append(item)
        self._persist_event(intent, item)

    def _transition(
        self,
        intent: ExecutionIntent,
        status: IntentStatus,
        *,
        event: str = "STATUS_CHANGED",
        detail: str | None = None,
        receipt: dict[str, Any] | None = None,
        trade_event: dict[str, Any] | None = None,
    ) -> ExecutionIntent:
        previous = intent.status
        intent.status = status
        intent.updated_at = datetime.now(timezone.utc)
        intent.revision += 1
        self._append_event(
            intent,
            event,
            previous,
            status,
            detail,
            at=intent.updated_at,
            receipt=receipt,
            trade_event=trade_event,
        )
        self._persist_state()
        return intent
