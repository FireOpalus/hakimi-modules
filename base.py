"""Base capability abstractions, registry, and mandatory anti-injection sensor firewall."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, Literal, final

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..context_budget import truncate_tokens
from ..models import Message
from ..security import clean_text, configured_secrets
from ..thinking_policy import CORE_THINKING_SCENES, ThinkingScene
from .actions import Action, ActionCatalog, DailyAction
from .scheduling import CLOCK_PATTERN, LocalActivity, ScheduleOptions

AttentionLevel = Literal["LOW", "MEDIUM", "HIGH"]
ATTENTION_RANK: dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
NodeRegion = Literal["KR", "CN"]

_AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*Bearer\s+[A-Za-z0-9._~+/=-]{4,}")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{4,}")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}")
_AIZA_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{8,}")
_CREDENTIAL_KEY_RE = re.compile(
    r"(?i)(?:[\"']?)"
    r"(?:api[_-]?key|apikey|password|passwd|pwd|secret|token|"
    r"sessdata|bili_jct|buvid3|buvid4|access[_-]?token|refresh[_-]?token|authorization)"
    r"(?:[\"']?)\s*[:=]\s*(?:[\"']?)[^\s,;}\"'\]]+(?:[\"']?)"
)
_ROLE_RE = re.compile(r"\[role=", re.IGNORECASE)
_CQ_RE = re.compile(r"\[cq:", re.IGNORECASE)
_AT_BROADCAST_RE = re.compile(r"@(everyone|all|全体成员)", re.IGNORECASE)
_COLLAPSE_WS_RE = re.compile(r"[\r\n\t ]+")


class GoalResult(BaseModel):
    """Structured result returned by a module after dispatching an intent goal."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool
    module_id: str = Field(min_length=1, max_length=64)
    summary: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


def sanitize_sensor_text(
    text: str, settings: Settings | None = None, *, max_tokens: int = 90
) -> str:
    """Scrub secrets, neutralize prompt/protocol injections, and bound token length."""
    if max_tokens <= 0:
        return ""
    cleaned = clean_text(str(text or ""))
    if not cleaned:
        return ""
    if settings is not None:
        candidates = {
            val
            for raw in configured_secrets(settings)
            if raw
            for val in (raw, clean_text(raw))
            if len(val) >= 6
        }
        for secret in sorted(candidates, key=len, reverse=True):
            if secret in cleaned:
                cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _AUTH_HEADER_RE.sub("[REDACTED]", cleaned)
    cleaned = _BEARER_RE.sub("[REDACTED]", cleaned)
    cleaned = _SK_RE.sub("[REDACTED]", cleaned)
    cleaned = _AIZA_RE.sub("[REDACTED]", cleaned)
    cleaned = _CREDENTIAL_KEY_RE.sub("[REDACTED]", cleaned)
    cleaned = _ROLE_RE.sub("[untrusted_role=", cleaned)
    cleaned = _CQ_RE.sub("［CQ:", cleaned)
    cleaned = _AT_BROADCAST_RE.sub(r"＠\1", cleaned)
    cleaned = html.escape(cleaned, quote=False)
    cleaned = _COLLAPSE_WS_RE.sub(" ", cleaned).strip()
    return truncate_tokens(cleaned, max_tokens)


class BaseModule(ABC):
    """Abstract capability module with enforced sensor sanitization and proactive lock hooks."""

    thinking_scenes: tuple[ThinkingScene, ...] = ()
    module_id: str = ""
    display_name: str = ""
    description: str = ""
    node_region: NodeRegion = "KR"
    attention_level: AttentionLevel = "LOW"
    proactive_paused: bool = False
    settings: Settings | None = None
    _registry: ModuleRegistry | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.proactive_paused = False
        self._registry = None

    @asynccontextmanager
    async def lifespan(self):
        """Initialize local resources before workers start and release them on shutdown."""
        yield

    chat_platforms: tuple[str, ...] = ()
    supports_reply_anchor: bool = False
    supports_voice: bool = False
    preferred_voice_format: str = "ogg_opus"
    supports_sticker: bool = False
    supports_rich_card: bool = False
    supports_emoji_like: bool = False
    supports_typing: bool = False
    supports_quick_ack: bool = False
    supports_presence: bool = False
    presence_platforms: tuple[str, ...] = ()
    admin_panel: str | None = None

    def authorize_chat(self, message, settings, *, bot_exempt=False) -> bool:
        return False

    def chat_connected(self, platform: str) -> bool:
        """Adapters that support unsolicited delivery report their live connection here."""
        return False

    def bot_id_for(self, platform: str) -> str:
        """Return the active bot account ID for this chat platform, if known."""
        return ""

    def offline_reason(self, platform: str) -> str:
        """Return the stable deferral reason code when this chat platform is disconnected."""
        return "plugin_disconnected"

    def presence_enabled(self) -> bool:
        """Return whether automatic public presence updates are currently enabled."""
        return False

    async def apply_presence(
        self,
        mood_text: str,
        *,
        kind: Literal["life_block", "digest", "patrol", "chat"] = "life_block",
    ) -> dict[str, Any] | None:
        """Optionally reflect current mood/activity onto the platform's public presence."""
        return None

    def permits_chat_export(self, message: Message, consumer: str) -> bool:
        """Permit a named consumer to read this admitted conversation for a separate purpose."""
        return False

    def permits_memory_mirror(self, message, settings) -> bool:
        return False

    async def send_chat(self, message, text, *, before_send):
        """Send literal text after the final gate and return the platform message ID as a string."""
        raise NotImplementedError

    async def send_voice(
        self,
        message: Message,
        audio_bytes: bytes,
        *,
        fmt: str,
        mime_type: str,
        duration_ms: int | None = None,
        before_send=None,
    ) -> str:
        """Send a voice message after the final gate and return the platform message ID."""
        raise NotImplementedError

    async def send_media(
        self,
        message: Message,
        media_bytes: bytes,
        *,
        kind: Literal["sticker", "card", "image"] = "image",
        mime_type: str = "image/png",
        filename: str = "media.png",
        reply_to: str | None = None,
        before_send=None,
    ) -> str:
        """Send an image/sticker/card after the final gate and return the platform message ID."""
        raise NotImplementedError

    async def send_emoji_like(
        self,
        message: Message,
        target_message_id: str,
        emoji_id: str,
        *,
        emoji: str | None = None,
        before_send=None,
    ) -> bool:
        """Optionally react to a platform message with an emoji."""
        return False

    async def send_typing(self, message: Message) -> None:
        """Optionally emit a best-effort typing indicator."""
        return None

    def admin_status(self, request) -> dict:
        """Local, display-safe snapshot for the authenticated module detail page."""
        return {}

    enabled: bool = True

    @property
    def configuration_issue(self) -> str | None:
        return None

    @property
    def inactive_reason(self) -> str | None:
        return self.configuration_issue if self.enabled else "module_disabled"

    schedule_options: type[ScheduleOptions] = ScheduleOptions
    recipient_namespaces: tuple[str, ...] = ()

    def scheduling(self, config) -> ScheduleOptions:
        defaults = self.schedule_options.from_settings(self.settings).model_dump()
        return self.schedule_options.model_validate(
            {**defaults, **config.module_settings.get(self.module_id, {})}
        )

    async def on_switch(self, service) -> None:
        """Reconcile platform work after the module switch has been persisted."""
        return None

    async def planning_context(self, service, config, now: float) -> dict:
        return {}

    def normalize_recipient(self, scope: str) -> str | None:
        return None

    def permits_recipient(self, scope: str, config) -> bool:
        return True

    def proactive_groups(self) -> list[str]:
        return []

    def local_activity(
        self, context: dict, available: set[str], unfunded: bool
    ) -> LocalActivity | None:
        return None

    def local_focus(self, context: dict, hour: int) -> tuple[AttentionLevel, str] | None:
        return None

    def actions(self) -> tuple[Action, ...]:
        return ()

    async def public_patrol_entry(self, event):
        """Optionally resolve a public event to its durable diary key and screened text."""
        return None

    async def current_activity(self) -> str | None:
        """Local status only. This hook must not call an external service."""
        return None

    async def social_material(self, ticket: dict) -> str:
        return await self.get_sensor_summary()

    async def context_data(self, *, limit: int, now: float, message: Message | None = None) -> dict:
        return {}

    def schedules(self) -> tuple[DailyAction, ...]:
        return ()

    def event_rules(self) -> tuple:
        return ()

    @final
    async def get_sensor_summary(self) -> str:
        """Return a strictly sanitized, <=90-token summary of current module sensor state."""
        raw = await self._raw_sensor_summary()
        return sanitize_sensor_text(raw, getattr(self, "settings", None), max_tokens=90)

    @abstractmethod
    async def _raw_sensor_summary(self) -> str:
        """Produce raw module status string before mandatory sanitization."""

    async def dispatch_goal(self, ticket: dict[str, Any]) -> GoalResult:
        """Compatibility entry for 2.0 module callers."""
        return GoalResult(ok=False, module_id=self.module_id, reason="unsupported_goal", summary="")

    async def pause_proactive(self) -> None:
        """Suspend autonomous proactive behavior when another high-attention task locks focus."""
        self.proactive_paused = True

    async def resume_proactive(self) -> None:
        """Resume autonomous proactive behavior when focus drops below HIGH."""
        self.proactive_paused = False

    def planning_fingerprint(self) -> str:
        """Return an optional low-frequency actionable state digest for planner wakeups."""
        return ""

    async def post_ledger_event(
        self,
        event_type: str,
        summary: str,
        *,
        payload: dict[str, Any] | None = None,
        target_module: str = "",
        dedupe_key: str | None = None,
        due_at: float | None = None,
        ttl_seconds: float = 3 * 86400.0,
        visibility: Literal["public", "owner_private"] = "public",
        now: float | None = None,
    ):
        """Post or update a cross-period stateful commitment in the built-in event ledger."""
        registry = getattr(self, "_registry", None)
        ledger = getattr(registry, "event_ledger", None) if registry is not None else None
        if ledger is None:
            raise RuntimeError("event_ledger_unavailable")
        return await ledger.post(
            self.module_id,
            event_type,
            summary,
            payload=payload,
            target_module=target_module,
            dedupe_key=dedupe_key,
            due_at=due_at,
            ttl_seconds=ttl_seconds,
            visibility=visibility,
            now=now,
        )

    async def read_ledger_events(
        self,
        *,
        source_module: str | None = None,
        target_module: str | None = None,
        event_type: str | None = None,
        only_due: bool = False,
        limit: int = 20,
        now: float | None = None,
    ) -> list[Any]:
        """Read public pending commitments from the built-in event ledger."""
        registry = getattr(self, "_registry", None)
        ledger = getattr(registry, "event_ledger", None) if registry is not None else None
        if ledger is None:
            return []
        return await ledger.read(
            source_module=source_module,
            target_module=target_module,
            event_type=event_type,
            include_owner_private=False,
            only_due=only_due,
            limit=limit,
            now=now,
        )

    async def consume_ledger_event(self, event_id_or_key: str, *, now: float | None = None) -> bool:
        """Consume a completed public commitment in the built-in event ledger."""
        registry = getattr(self, "_registry", None)
        ledger = getattr(registry, "event_ledger", None) if registry is not None else None
        if ledger is None:
            return False
        return await ledger.consume(
            event_id_or_key,
            consumer=self.module_id,
            allow_owner_private=False,
            now=now,
        )


class ModuleRegistry:
    """In-memory registry of capability modules for sensor collection and attention locking."""

    def __init__(self, *, owner_delivery=None) -> None:
        self.owner_delivery = owner_delivery
        self._owner_slots = {}
        self._modules: dict[str, BaseModule] = {}
        self.chat_platforms = {}
        self.systems = {}
        self.public_event_observers = {}
        self.thinking_scenes = {scene.key: scene for scene in CORE_THINKING_SCENES}
        self._module_scenes: dict[str, tuple[str, ...]] = {}
        self.catalog = ActionCatalog(
            self.inactive_reason,
            chat_platforms=lambda: self.chat_platforms.keys(),
        )
        self.rule_owners: dict[str, str] = {}
        self.rules = {}
        self._recipients: dict[str, BaseModule] = {}
        self.event_ledger: Any = None

    def register_system(self, module) -> None:
        if (
            not module.module_id
            or module.module_id in self.systems
            or module.module_id in self._modules
        ):
            raise ValueError("module_id must be non-empty and unique")
        scenes = self._new_scenes(module)
        observers = module.public_event_observers()
        if observers.keys() & (self.public_event_observers.keys() | self.rules.keys()):
            raise ValueError("duplicate event rule")
        self.systems[module.module_id] = module
        self.thinking_scenes.update(scenes)
        self.public_event_observers.update(observers)

    def register(self, module: BaseModule) -> None:
        """Register a capability module by its unique module_id."""
        if (
            not module.module_id
            or module.module_id in self._modules
            or module.module_id in self.systems
        ):
            raise ValueError("module_id must be non-empty and unique")
        protocols = set(module.chat_platforms)
        if (
            len(protocols) != len(module.chat_platforms)
            or protocols.intersection(self.chat_platforms)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", p) for p in protocols)
            or (protocols.intersection({"onebot", "qq"}) and module.module_id != "qq_social")
        ):
            raise ValueError("invalid or duplicate chat platform")
        namespaces = set(module.recipient_namespaces)
        if (
            "boundowner" in namespaces
            or len(namespaces) != len(module.recipient_namespaces)
            or namespaces.intersection(self._recipients)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in namespaces)
        ):
            raise ValueError("invalid or duplicate recipient namespace")
        scenes = self._new_scenes(module)
        actions = module.actions()
        supplement = self.owner_delivery.module_timer(module) if self.owner_delivery else None
        if supplement:
            actions = (*actions, supplement[0])
        for action in actions:
            for quota in action.quota_rules():
                if quota.counter in {"total", "counted"}:
                    raise ValueError("reserved activity counter")
                if quota.unlimited_option:
                    field = module.schedule_options.model_fields.get(quota.unlimited_option)
                    if field is None or field.annotation is not bool:
                        raise ValueError("quota switch must be a declared boolean module setting")
        timer_actions = {action.name for action in actions if "timer" in action.origins}
        fields = module.schedule_options.model_json_schema()["properties"]
        for slot in module.schedules():
            if slot.name not in timer_actions:
                raise ValueError("daily schedule requires a local timer action")
            if (
                slot.time_option
                and fields.get(slot.time_option, {}).get("pattern") != CLOCK_PATTERN
            ):
                raise ValueError("daily schedule requires a ScheduleTime field")
        rules = module.event_rules()
        names = {rule.name for rule in rules}
        if (
            len(names) != len(rules)
            or names.intersection(self.public_event_observers)
            or names.intersection(self.rules)
        ):
            raise ValueError("duplicate event rule")
        self.catalog.install(module.module_id, actions)
        self.rule_owners.update(dict.fromkeys(names, module.module_id))
        self.rules.update({rule.name: rule for rule in rules})
        self._recipients.update(dict.fromkeys(namespaces, module))
        self.chat_platforms.update(dict.fromkeys(protocols, module))
        if supplement:
            self._owner_slots[module.module_id] = supplement[1]
        module._registry = self
        self._modules[module.module_id] = module
        self._module_scenes[module.module_id] = tuple(scenes)
        self.thinking_scenes.update(scenes)

    def unregister(self, module_id: str) -> bool:
        """Unregister a non-system capability module and cleanly undo register() state."""
        if module_id in self.systems:
            raise ValueError(f"cannot unregister system module: {module_id}")
        module = self._modules.pop(module_id, None)
        if module is None:
            return False

        self.catalog.uninstall(module_id)

        core_keys = {scene.key for scene in CORE_THINKING_SCENES}
        system_keys = {
            scene.key for sys_mod in self.systems.values() for scene in sys_mod.thinking_scenes
        }
        scene_keys = self._module_scenes.pop(module_id, None)
        if scene_keys is None:
            scene_keys = tuple(scene.key for scene in module.thinking_scenes)
        for key in scene_keys:
            if key not in core_keys and key not in system_keys:
                self.thinking_scenes.pop(key, None)

        for platform in [name for name, owner in self.chat_platforms.items() if owner is module]:
            self.chat_platforms.pop(platform, None)

        for namespace in [name for name, owner in self._recipients.items() if owner is module]:
            self._recipients.pop(namespace, None)

        for rule_name in [name for name, owner in self.rule_owners.items() if owner == module_id]:
            self.rule_owners.pop(rule_name, None)
            self.rules.pop(rule_name, None)

        self._owner_slots.pop(module_id, None)
        module._registry = None
        return True

    def _new_scenes(self, module):
        scenes = {scene.key: scene for scene in module.thinking_scenes}
        if (
            len(scenes) != len(module.thinking_scenes)
            or self.thinking_scenes.keys() & scenes.keys()
        ):
            raise ValueError("duplicate thinking scene")
        return scenes

    def get(self, module_id: str) -> BaseModule | None:
        """Return the registered module matching module_id, or None."""
        return self._modules.get(module_id)

    def all(self) -> list[BaseModule]:
        """Return all registered modules in registration order."""
        return list(self._modules.values())

    def inactive_reason(self, module_id: str) -> str | None:
        module = self.get(module_id)
        return module.inactive_reason if module else "module_not_registered"

    def active(self) -> list[BaseModule]:
        return [module for module in self.all() if module.inactive_reason is None]

    def _presence_module(self, platform: str) -> BaseModule | None:
        module = self.chat_platforms.get(platform)
        if module is None or module.inactive_reason is not None:
            return None
        if not bool(getattr(module, "supports_presence", False)):
            return None
        allowed_platforms = getattr(module, "presence_platforms", ())
        if allowed_platforms and platform not in allowed_platforms:
            return None
        return module

    def supports_presence_for(self, platform: str) -> bool:
        return self._presence_module(platform) is not None

    def presence_enabled_for(self, platform: str) -> bool:
        module = self._presence_module(platform)
        return bool(module is not None and module.presence_enabled())

    async def apply_presence(
        self,
        mood_text: str,
        *,
        kind: Literal["life_block", "digest", "patrol", "chat"] = "life_block",
        platform: str | None = None,
    ) -> dict[str, Any] | None:
        if platform is not None:
            module = self._presence_module(platform)
            if module is None:
                return None
            try:
                return await module.apply_presence(mood_text, kind=kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "event=presence_apply_failed module=%s platform=%s kind=%s error=%s",
                    module.module_id,
                    platform,
                    kind,
                    type(exc).__name__,
                )
                return None

        results: dict[str, Any] = {}
        for module in self.active():
            if not getattr(module, "supports_presence", False):
                continue
            try:
                results[module.module_id] = await module.apply_presence(mood_text, kind=kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "event=presence_apply_failed module=%s kind=%s error=%s",
                    module.module_id,
                    kind,
                    type(exc).__name__,
                )
        return results or None

    def schedules_for(self, module):
        slots = module.schedules()
        extra = self._owner_slots.get(module.module_id)
        return (*slots, extra) if extra else slots

    def _settings(self):
        if self.owner_delivery is not None and getattr(self.owner_delivery, "s", None) is not None:
            return self.owner_delivery.s
        for module in self._modules.values():
            if getattr(module, "settings", None) is not None:
                return module.settings
        return None

    def normalize_recipient(self, scope: str) -> str | None:
        raw = str(scope or "").strip()
        if not raw or raw.startswith("channel:"):
            return None
        if self.owner_delivery and self.owner_delivery.external_scope(raw):
            return raw
        if raw.startswith("boundowner:"):
            settings = self._settings()
            if settings is not None:
                from ..social import resolve_owner_scope

                matched = resolve_owner_scope(raw, settings)
                if matched is not None:
                    plat, _ = matched
                    mod = self.chat_platforms.get(plat)
                    if mod is not None and mod.inactive_reason is None:
                        return raw
            return None
        if raw in {"owner", "owner_dm"}:
            settings = self._settings()
            if settings is not None:
                from ..social import RecipientResolver

                hit = RecipientResolver(settings, self).resolve_owner(raw)
                if hit is not None:
                    plat, _, canonical = hit
                    mod = self.chat_platforms.get(plat)
                    if mod is not None and mod.inactive_reason is None:
                        return canonical
                return None
        # Canonical addresses belong to one transport. Its checker also handles legacy aliases.
        owner = self._recipients.get(raw.partition(":")[0])
        providers = (
            ([owner] if owner.inactive_reason is None else ())
            if owner is not None
            else (
                module
                for module in self.all()
                if module.recipient_namespaces and module.inactive_reason is None
            )
        )
        candidates = set()
        for module in providers:
            canonical = module.normalize_recipient(raw)
            if canonical and self._recipients.get(canonical.partition(":")[0]) is module:
                candidates.add(canonical)
        return candidates.pop() if len(candidates) == 1 else None

    def owner_delivery_enabled(self, config, *, settings=None, engine=None) -> bool:
        s = settings if settings is not None else self._settings()
        if (
            engine is not None
            and not getattr(engine, "overridden", False)
            and getattr(config, "module_settings", None)
            == getattr(getattr(engine, "env_config", None), "module_settings", None)
        ):
            return bool(getattr(s, "proactive_dm_enabled", True))
        social_mod = self.get("social_presence")
        if social_mod is not None:
            return bool(social_mod.scheduling(config).owner_delivery_enabled)
        from .scheduling import SocialScheduleOptions

        defaults = SocialScheduleOptions.from_settings(s).model_dump()
        raw = (getattr(config, "module_settings", None) or {}).get("social_presence", {})
        if raw:
            return bool(
                SocialScheduleOptions.model_validate({**defaults, **raw}).owner_delivery_enabled
            )
        return bool(
            getattr(s, "proactive_dm_enabled", True)
            and getattr(config, "owner_delivery_enabled", True)
        )

    def _owner_delivery_enabled(self, config) -> bool:
        return self.owner_delivery_enabled(config)

    def permits_recipient(self, scope: str, config) -> bool:
        if self.owner_delivery and self.owner_delivery.external_scope(scope):
            return self.owner_delivery.delivery_enabled
        if isinstance(scope, str) and scope.startswith("boundowner:"):
            settings = self._settings()
            if settings is None:
                return False
            from ..social import resolve_owner_scope

            matched = resolve_owner_scope(scope, settings)
            if matched is None:
                return False
            plat, _ = matched
            mod = self.chat_platforms.get(plat)
            if mod is None or mod.inactive_reason is not None:
                return False
            return self._owner_delivery_enabled(config)
        module = self._recipients.get(scope.partition(":")[0])
        return bool(
            module and module.inactive_reason is None and module.permits_recipient(scope, config)
        )

    async def context_data(self, *, limit: int, now: float, message: Message | None = None) -> dict:
        # Context providers receive an authenticated destination without message content.
        audience = None
        if message is not None:
            audience = Message(
                platform=message.platform,
                bot_id=message.bot_id,
                kind=message.kind,
                conversation_id=message.conversation_id,
                user_id=message.user_id if message.kind == "private" else "0",
                text="",
                event_id="context",
                created_at=now,
                addressed=False,
            )
        data = {}
        for module in self.active():
            try:
                material = dict(await module.context_data(limit=limit, now=now, message=audience))
                # Saved prompts and their continuation digests use this original Bili field.
                if module.module_id == "bilibili_surf" and "recent_bili_surfed" in material:
                    data["recent_bili_surfed"] = material.pop("recent_bili_surfed")
                if material:
                    excerpt = sanitize_sensor_text(
                        json.dumps(material, ensure_ascii=False, allow_nan=False),
                        module.settings,
                        max_tokens=180,
                    )
                    data.setdefault("module_context", {})[module.module_id] = excerpt
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "event=context_provider_unavailable module=%s error=%s",
                    module.module_id,
                    type(exc).__name__,
                )
        return data

    async def collect_sensor_summaries(self) -> dict[str, str]:
        """Collect sanitized sensor summaries across all registered modules."""
        summaries: dict[str, str] = {}
        for module in self.active():
            summaries[module.module_id] = await module.get_sensor_summary()
        return summaries
