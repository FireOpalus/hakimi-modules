"""Shared capability SDK and the stable Python namespace for the four platform repositories."""

from pathlib import Path

from .base import (
    ATTENTION_RANK,
    AttentionLevel,
    BaseModule,
    GoalResult,
    ModuleRegistry,
    NodeRegion,
    sanitize_sensor_text,
)

# Each pinned repository contributes files to hakimi.modules. Keep their original
# import names so integrations, type identities and serialized references survive the split.
_platforms = Path(__file__).parent / "platforms"
__path__.extend(str(_platforms / name) for name in ("qq", "bilibili", "minecraft", "weather"))

from .bilibili_surf import BilibiliSurfModule  # noqa: E402
from .minecraft_slot import MinecraftSlotModule, MinecraftSnapshot  # noqa: E402
from .qq_social import QQSocialModule  # noqa: E402

__all__ = [
    "ATTENTION_RANK",
    "AttentionLevel",
    "BaseModule",
    "BilibiliSurfModule",
    "GoalResult",
    "MinecraftSlotModule",
    "MinecraftSnapshot",
    "ModuleRegistry",
    "NodeRegion",
    "QQSocialModule",
    "sanitize_sensor_text",
]
