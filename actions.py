"""Trusted, in-process action declarations. A model selects a name, never a callable."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .scheduling import CLOCK_PATTERN, ActivityQuota


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


Origin = Literal["planner", "timer", "rule", "admin", "legacy"]
Mode = Literal["active", "shadow", "legacy", "off"]
AUTONOMOUS = frozenset({"planner", "rule", "timer", "legacy"})
NAME = re.compile(r"[a-z][a-z0-9_]{0,30}\.[a-z][a-z0-9_]{0,30}\Z")


@dataclass(frozen=True)
class ActionCall:
    ticket: dict
    args: BaseModel
    now: float
    evidence: dict = field(default_factory=dict)
    desktop_hint: str | None = None
    still_allowed: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class Outcome:
    status: Literal["done", "gated_skip", "not_dispatched", "uncertain", "executing", "expired"]
    reason: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    reason: str = ""
    delay: int = 0
    desktop_hint: str | None = None


Handler = Callable[[ActionCall], Awaitable[Outcome]]
Alias = tuple[str, str, bool]  # exact 2.0 (target_module, action_type, has_recipient)


@dataclass(frozen=True, kw_only=True)
class Action:
    name: str
    description: str
    run: Handler
    arguments: type[BaseModel] = NoArgs
    social: bool = False
    proactive_speak: bool = False
    owner_only: bool = False
    budget_lane: Literal["patrol", "proactive"] | None = None
    # A previously attempted operation may have a durable result and need no further paid call.
    reuses_paid_work: Callable[[float], Awaitable[bool]] | None = None
    window: Literal["any", "active", "closing"] = "any"
    interrupts_focus: bool = False
    effortful: bool = False
    background_sensor: bool = False
    # Paid or social actions share the daily allowance by default; () opts out.
    quotas: tuple[ActivityQuota, ...] | None = None
    default_mode: Mode = "active"
    origins: frozenset[str] = AUTONOMOUS
    unavailable: Callable[[], str | None] = lambda: None
    preflight: Callable[[dict, float, Any], Awaitable[GateDecision]] | None = None
    aliases: tuple[Alias, ...] = ()
    name_aliases: tuple[str, ...] = ()

    def quota_rules(self) -> tuple[ActivityQuota, ...]:
        if self.quotas is not None:
            return self.quotas
        return (ActivityQuota(),) if self.social or self.budget_lane else ()


class ActionCatalog:
    def __init__(
        self,
        inactive_reason: Callable[[str], str | None] = lambda _: None,
        chat_platforms: Callable[[], Iterable[str]] = lambda: (),
    ):
        self.inactive_reason = inactive_reason
        self._chat_platforms = chat_platforms
        self.actions: dict[str, Action] = {}
        self.owners: dict[str, str] = {}
        self.aliases: dict[Alias, str] = {}
        self.name_aliases: dict[str, str] = {}

    def install(self, module_id: str, actions: tuple[Action, ...]) -> None:
        names, aliases = set(self.actions), set(self.aliases)
        name_aliases = set(self.name_aliases)
        for spec in actions:
            if not NAME.fullmatch(spec.name) or spec.name in names or spec.name in name_aliases:
                raise ValueError(f"invalid or duplicate action: {spec.name}")
            if spec.proactive_speak and not spec.social:
                raise ValueError("proactive_speak actions must be social")
            if "query" in spec.origins and (
                spec.social or spec.owner_only or spec.budget_lane or spec.arguments is not NoArgs
            ):
                raise ValueError("module queries must be internal, unmetered and take no arguments")
            if spec.background_sensor and (
                spec.origins != {"timer"} or spec.social or spec.budget_lane or spec.window != "any"
            ):
                raise ValueError("background sensors must be internal unmetered timer actions")
            names.add(spec.name)
            for alias in spec.aliases:
                if alias in aliases or alias[2] != spec.social:
                    raise ValueError(f"invalid or duplicate action alias: {alias}")
                aliases.add(alias)
            for alias_name in spec.name_aliases:
                if (
                    not NAME.fullmatch(alias_name)
                    or alias_name in names
                    or alias_name in name_aliases
                ):
                    raise ValueError(f"invalid or duplicate action name alias: {alias_name}")
                name_aliases.add(alias_name)
        for spec in actions:
            self.actions[spec.name] = spec
            self.owners[spec.name] = module_id
            self.aliases.update(dict.fromkeys(spec.aliases, spec.name))
            self.name_aliases.update(dict.fromkeys(spec.name_aliases, spec.name))

    def uninstall(self, module_id: str) -> None:
        """Remove all actions, owners, and aliases registered by module_id."""
        owned = {name for name, owner in self.owners.items() if owner == module_id}
        if not owned:
            return
        for name in owned:
            self.actions.pop(name, None)
            self.owners.pop(name, None)
        for alias in [key for key, target in self.aliases.items() if target in owned]:
            self.aliases.pop(alias, None)
        for alias_name in [key for key, target in self.name_aliases.items() if target in owned]:
            self.name_aliases.pop(alias_name, None)

    def canonical_name(self, name: str | None) -> str | None:
        if not name:
            return None
        if name in self.actions:
            return name
        if mapped := self.name_aliases.get(name):
            return mapped
        if "social.speak" in self.actions and name.endswith(".speak") and NAME.fullmatch(name):
            plat = name[:-6]
            if plat in set(self._chat_platforms()) or (plat, "speak", True) in self.aliases:
                return "social.speak"
        return name

    def get(self, name: str | None) -> Action | None:
        canon = self.canonical_name(name)
        return self.actions.get(canon or "")

    def old_name(self, ticket: dict) -> str | None:
        mapped = self.aliases.get(
            (ticket["target_module"], ticket["action_type"], bool(ticket["target_scope"]))
        )
        if mapped is not None:
            return mapped
        mod = ticket.get("target_module")
        if (
            "social.speak" in self.actions
            and ticket.get("action_type") == "speak"
            and bool(ticket.get("target_scope"))
            and isinstance(mod, str)
            and mod
            and mod in set(self._chat_platforms())
            and NAME.fullmatch(f"{mod}.speak")
            and f"{mod}.speak" not in self.actions
        ):
            return "social.speak"
        return None

    def resolve(self, ticket: dict, *, compatibility: bool = False) -> Action:
        raw_name = ticket.get("action_name")
        canon_raw = self.canonical_name(raw_name) if raw_name else None
        name = self.old_name(ticket) if compatibility else (canon_raw or self.old_name(ticket))
        spec = self.actions.get(name or "")
        if not spec:
            raise ValueError("unknown_action")
        if reason := self.inactive_reason(self.owners[spec.name]):
            raise ValueError(reason)
        if ticket.get("origin", "legacy") not in spec.origins:
            raise ValueError("action_origin_forbidden")
        if spec.social != bool(ticket.get("target_scope")):
            raise ValueError("invalid_action_audience")
        if compatibility and raw_name not in (None, "", name) and canon_raw != name:
            raise ValueError("incompatible_action")
        return spec

    def view(
        self, modes: dict[str, Mode], *, planner: bool = False, module_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for name, spec in sorted(self.actions.items()):
            if module_id is not None and self.owners[name] != module_id:
                continue
            mode = modes.get(name, "active")
            reason = self.inactive_reason(self.owners[name]) or (
                "action_disabled" if mode == "off" else spec.unavailable()
            )
            if planner and (reason or "planner" not in spec.origins):
                continue
            schema = spec.arguments.model_json_schema()
            audience = "social" if spec.social else "internal"
            if spec.owner_only:
                audience = "owner"
            rows.append(
                {
                    "name": name,
                    "module": self.owners[name],
                    "description": spec.description,
                    "audience": audience,
                    "budget_lane": spec.budget_lane,
                    "effortful": spec.effortful,
                    "arguments": schema["properties"],
                    "required": schema.get("required", []),
                    "mode": mode,
                    "available": reason is None,
                    "reason": reason,
                    "origins": sorted(spec.origins),
                    "compatibility": bool(spec.aliases),
                }
            )
        return rows


@dataclass(frozen=True)
class DailyReceipt:
    """Public execution evidence a module elects to expose for its daily slot."""

    day: str
    stage: str
    started: float | None = None
    confirmed: float | None = None


@dataclass(frozen=True)
class DailyAction:
    """A module-provided local schedule; idempotency uses the local day and slot."""

    name: str
    at: str | None  # None closes the active window for the local day.
    ttl: int = 1800  # Fixed-time actions; a closing action expires at the end of its window.
    target_scope: str = ""
    label: str = ""
    attempted: Callable[[str], Awaitable[bool]] | None = None
    enabled: Callable[[], bool] | None = None

    time_option: str = ""  # The declared at value also identifies this daily slot in old receipts.
    receipts: Callable[[], Awaitable[list[DailyReceipt]]] | None = None

    def __post_init__(self):
        if self.ttl <= 0 or (self.time_option and self.at is None):
            raise ValueError("invalid daily schedule")
        if self.at is not None and not re.fullmatch(CLOCK_PATTERN, self.at):
            raise ValueError("expected HH:MM daily schedule")

    def effective_time(self, options):
        return getattr(options, self.time_option) if self.time_option else self.at


@dataclass(frozen=True)
class HourlyAction(DailyAction):
    """One timer slot per elapsed hour, anchored to the local clock."""

    at: str = field(default="00:00", init=False)
    ttl: int = field(default=3600, init=False)
    time_option: str = field(default="", init=False)


def reject_reason(exc: ValueError) -> str:
    # Pydantic diagnostics may include submitted values. Only send constant reason codes to admin.
    codes = {
        "unknown_action",
        "module_disabled",
        "weather_location_missing",
        "action_origin_forbidden",
        "invalid_action_audience",
        "incompatible_action",
        "action_disabled",
        "unauthorized_scope",
        "invalid_action_arguments",
    }
    return str(exc) if type(exc) is ValueError and str(exc) in codes else "invalid_action_arguments"
