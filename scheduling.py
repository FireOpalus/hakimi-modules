"""Typed scheduling controls and quota declarations owned by registered modules."""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CLOCK_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
ScheduleTime = Annotated[str, Field(pattern=CLOCK_PATTERN)]


class ScheduleOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    @classmethod
    def from_settings(cls, settings):
        return cls()


class SocialScheduleOptions(ScheduleOptions):
    owner_delivery_enabled: bool = Field(True, title="允许向 Owner 投递私信与运维告警")
    owner_dms_unlimited: bool = Field(False, title="Owner 主动私信不限次数")
    group_cooldown_seconds: int = Field(1500, ge=300, le=86400, title="群聊主动冷却（秒）")
    group_active_window_seconds: int = Field(1800, ge=60, le=7200, title="群聊活跃观察窗口（秒）")
    group_min_recent_messages: int = Field(2, ge=1, le=50, title="群聊最少有效消息数")
    desktop_active_suppress_seconds: int = Field(
        7200, ge=300, le=86400, title="桌面活跃抑制窗口（秒）"
    )

    @classmethod
    def from_settings(cls, settings):
        if settings is None:
            return cls()
        return cls(
            owner_delivery_enabled=settings.proactive_dm_enabled,
            owner_dms_unlimited=settings.life_engine_owner_dms_unlimited,
        )


@dataclass(frozen=True)
class ActivityQuota:
    """First matching rule applies. An empty option always uses the shared allowance."""

    counter: str = ""
    unlimited_option: str = ""
    owner_only: bool = False


@dataclass(frozen=True)
class LocalActivity:
    """An optional offline suggestion. The planner still validates the chosen action."""

    priority: int
    intent: dict[str, Any]
    one_outstanding: bool = False
    focus: tuple[Literal["LOW", "MEDIUM", "HIGH"], str] | None = None
