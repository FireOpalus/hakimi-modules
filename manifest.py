"""Read the catalog's committed Git submodule list without Git or application dependencies."""

import re
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

CATALOG = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Platform:
    name: str
    url: str
    legacy_imports: bool

    @property
    def path(self) -> str:
        return f"platforms/{self.name}"


def read_manifest(catalog: Path = CATALOG) -> tuple[Platform, ...]:
    config = ConfigParser(interpolation=None)
    with (catalog / ".gitmodules").open(encoding="utf-8") as stream:
        config.read_file(stream)
    platforms = {}
    for section in config.sections():
        entry = config[section]
        match = re.fullmatch(r"platforms/([a-z][a-z0-9_]{0,47})", entry.get("path", ""))
        if not match or not section.startswith('submodule "') or not entry.get("url"):
            raise ValueError(f"Invalid platform entry in .gitmodules: {section}")
        name = match[1]
        if name in platforms:
            raise ValueError(f"Duplicate platform path: {name}")
        platforms[name] = Platform(
            name, entry["url"], entry.getboolean("legacy-flat-imports", False)
        )
    return tuple(platforms.values())


def check_entry(catalog: Path, platform: Platform) -> None:
    entry = catalog / platform.path / "plugin.py"
    if not entry.is_file() or entry.resolve() != entry:
        raise ValueError(f"Missing or invalid {platform.path}/plugin.py; check the pinned checkout")
