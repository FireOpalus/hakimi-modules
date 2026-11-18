"""Capability SDK and the import namespace shared with installed platforms."""

from importlib import import_module

from .base import (
    ATTENTION_RANK,
    AttentionLevel,
    BaseModule,
    GoalResult,
    ModuleRegistry,
    NodeRegion,
    sanitize_sensor_text,
)
from .manifest import CATALOG, check_entry, read_manifest

# Old public imports retain their class identity. New plugins use their own package namespace.
PLATFORMS = read_manifest()
__path__.extend(str(CATALOG / p.path) for p in PLATFORMS if p.legacy_imports)

_legacy_exports = {
    "BilibiliSurfModule": "bilibili_surf",
    "MinecraftSlotModule": "minecraft_slot",
    "MinecraftSnapshot": "minecraft_slot",
    "QQSocialModule": "qq_social",
}


def __getattr__(name):
    if name not in _legacy_exports:
        raise AttributeError(name)
    return getattr(import_module(f".{_legacy_exports[name]}", __name__), name)


def platform_entries():
    for platform in PLATFORMS:
        check_entry(CATALOG, platform)
        yield platform, import_module(f".platforms.{platform.name}.plugin", __name__)


def install_modules(service) -> None:
    for platform, entry in platform_entries():
        factory = getattr(entry, "create_module", None)
        if not callable(factory):
            raise ValueError(f"{platform.path}/plugin.py must export create_module(service)")
        module = factory(service)
        if not isinstance(module, BaseModule):
            raise TypeError(f"{platform.path}/plugin.py must return a BaseModule")
        service.modules.register(module)


__all__ = [
    "ATTENTION_RANK",
    "AttentionLevel",
    "BaseModule",
    "GoalResult",
    "ModuleRegistry",
    "NodeRegion",
    "install_modules",
    "platform_entries",
    "sanitize_sensor_text",
] + list(_legacy_exports)
