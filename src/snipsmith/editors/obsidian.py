"""obsidian + latex suite: vault discovery, plugin install, and settings."""

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from ..errors import EditorError
from ..paths import app_data_root

NOT_FOUND = "no vaults found"
PLUGIN_ID = "obsidian-latex-suite"
PLUGIN_REPO = "artisticat1/obsidian-latex-suite"
PLUGIN_ASSETS = ("main.js", "manifest.json", "styles.css")
DEFAULT_SNIPPETS = "snipsmith-snippets.js"
DEFAULT_VARIABLES = "snipsmith-variables.json"


def _fetch(url: str) -> bytes:
    from urllib.request import Request, urlopen

    with urlopen(Request(url, headers={"User-Agent": "snipsmith"}), timeout=30) as response:
        return response.read()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def config_files() -> list[Path]:
    """candidate locations of obsidian.json, which lists the user's vaults."""
    files = [app_data_root() / "obsidian/obsidian.json"]
    if sys.platform.startswith("linux"):
        home = Path.home()
        files += [
            home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",
            home / "snap/obsidian/current/.config/obsidian/obsidian.json",
        ]
    return files


@dataclass
class Vault:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def detail(self) -> str:
        return str(self.path)

    @property
    def config_dir(self) -> Path:
        return self.path / ".obsidian"

    @property
    def plugin_dir(self) -> Path:
        return self.config_dir / "plugins" / PLUGIN_ID

    @property
    def plugin_installed(self) -> bool:
        return (self.plugin_dir / "main.js").exists()

    @property
    def plugin_version(self) -> str | None:
        return (_read_json(self.plugin_dir / "manifest.json") or {}).get("version")

    @property
    def community_plugins_enabled(self) -> bool:
        return (self.config_dir / "community-plugins.json").exists()

    def _enabled_plugins(self) -> list[str]:
        plugins = _read_json(self.config_dir / "community-plugins.json")
        return plugins if isinstance(plugins, list) else []

    @property
    def plugin_enabled(self) -> bool:
        return PLUGIN_ID in self._enabled_plugins()

    def status(self) -> str:
        if not self.plugin_installed:
            return "latex suite not installed"
        state = "enabled" if self.plugin_enabled else "installed but disabled"
        return f"latex suite {self.plugin_version or '?'} {state}"

    def contains(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.path.resolve())

    def relative(self, path: Path) -> str:
        """vault-relative, forward-slash path as latex suite expects it."""
        return path.resolve().relative_to(self.path.resolve()).as_posix()

    def install(self, fetch: Callable[[str], bytes] | None = None) -> str:
        """download the latest release into the vault."""
        fetch = fetch or _fetch
        base = f"https://github.com/{PLUGIN_REPO}/releases/latest/download/"
        try:
            contents = {name: fetch(base + name) for name in PLUGIN_ASSETS}
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            for name, data in contents.items():
                (self.plugin_dir / name).write_bytes(data)
            version = json.loads(contents["manifest.json"]).get("version", "?")
        except (OSError, ValueError) as e:
            raise EditorError(
                f"could not install latex suite ({e}); install it from obsidian's "
                f"community plugins browser instead"
            ) from e
        return f"installed latex suite {version}"

    def enable(self) -> bool:
        """add latex suite to community-plugins.json; returns whether it was missing."""
        plugins = self._enabled_plugins()
        if PLUGIN_ID in plugins:
            return False
        _write_json(self.config_dir / "community-plugins.json", [*plugins, PLUGIN_ID])
        return True

    def configure(self, snippets_file: Path, variables_file: Path) -> None:
        """point latex suite at the generated files (both must be inside the vault)."""
        data_file = self.plugin_dir / "data.json"
        settings = _read_json(data_file) or {}
        settings.update(
            {
                "loadSnippetsFromFile": True,
                "snippetsFileLocation": self.relative(snippets_file),
                "loadSnippetVariablesFromFile": True,
                "snippetVariablesFileLocation": self.relative(variables_file),
            }
        )
        _write_json(data_file, settings)


def detect() -> list[Vault]:
    vaults: list[Vault] = []
    for config in config_files():
        data = _read_json(config) or {}
        for entry in (data.get("vaults") or {}).values():
            path = Path(entry.get("path", ""))
            if path.is_dir() and path not in [v.path for v in vaults]:
                vaults.append(Vault(path))
    return vaults


def is_running() -> bool:
    try:
        return (
            subprocess.run(["pgrep", "-i", "-x", "obsidian"], capture_output=True).returncode == 0
        )
    except FileNotFoundError:
        return False
