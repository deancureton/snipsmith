"""vscode-family editors + hypersnips: detection and extension install."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from ..errors import EditorError
from ..paths import app_data_root

NOT_FOUND = "no vscode-family editors found"
EXTENSION_ID = "draivin.hsnips"
DEFAULT_FILES = ("latex.hsnips", "markdown.hsnips")
SUFFIX = ".hsnips"


@dataclass(frozen=True)
class Variant:
    name: str
    data_dir: str  # folder under the platform's app data root
    cli: str
    ext_home: str  # dotfolder under $HOME holding extensions/
    app: str  # macos bundle name
    flatpak: str | None = None  # linux flatpak app id

    def user_dirs(self) -> list[Path]:
        """candidate User/ directories, most conventional first."""
        dirs = [app_data_root() / self.data_dir / "User"]
        if sys.platform.startswith("linux"):
            if self.flatpak:
                dirs.append(
                    Path.home() / ".var/app" / self.flatpak / "config" / self.data_dir / "User"
                )
            dirs.append(
                Path.home() / "snap" / self.cli / "current/.config" / self.data_dir / "User"
            )
        return dirs


VARIANTS = [
    Variant(
        "Visual Studio Code",
        "Code",
        "code",
        ".vscode",
        "Visual Studio Code.app",
        flatpak="com.visualstudio.code",
    ),
    Variant(
        "VS Code Insiders",
        "Code - Insiders",
        "code-insiders",
        ".vscode-insiders",
        "Visual Studio Code - Insiders.app",
    ),
    Variant("Cursor", "Cursor", "cursor", ".cursor", "Cursor.app"),
    Variant(
        "VSCodium",
        "VSCodium",
        "codium",
        ".vscode-oss",
        "VSCodium.app",
        flatpak="com.vscodium.codium",
    ),
    Variant("Windsurf", "Windsurf", "windsurf", ".windsurf", "Windsurf.app"),
    Variant("Antigravity", "Antigravity", "antigravity", ".antigravity-ide", "Antigravity.app"),
]


def _load_jsonc(path: Path) -> dict:
    """best-effort parse of a vscode settings.json (comments, trailing commas)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        pass
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Editor:
    variant: Variant
    user_dir: Path
    cli: str | None

    @property
    def name(self) -> str:
        return self.variant.name

    @property
    def detail(self) -> str | None:
        return None

    @property
    def extensions_dir(self) -> Path:
        return Path.home() / self.variant.ext_home / "extensions"

    @property
    def extension_installed(self) -> bool:
        return any(self.extensions_dir.glob(f"{EXTENSION_ID}-*"))

    @property
    def hsnips_dir(self) -> Path:
        """where hsnips reads snippet files: hsnips.hsnipsPath or its default."""
        custom = _load_jsonc(self.user_dir / "settings.json").get("hsnips.hsnipsPath")
        if isinstance(custom, str) and custom:
            path = Path(os.path.expandvars(custom)).expanduser()
            if path.is_absolute():
                return path
        return self.user_dir / "globalStorage" / EXTENSION_ID / "hsnips"

    def status(self) -> str:
        parts = ["hsnips installed" if self.extension_installed else "hsnips not installed"]
        if not self.cli:
            parts.append(f"no `{self.variant.cli}` command on PATH")
        return ", ".join(parts)

    def install(self) -> str:
        if not self.cli:
            raise EditorError(
                f'the `{self.variant.cli}` command is not available; run "Shell Command: '
                f"Install '{self.variant.cli}' command in PATH\" from the command palette and "
                f"rerun, or install hsnips from the extensions view"
            )
        result = subprocess.run(
            [self.cli, "--install-extension", EXTENSION_ID, "--force"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise EditorError(f"install failed: {(result.stderr or result.stdout).strip()}")
        return "installed hypersnips"


def _find_cli(variant: Variant) -> str | None:
    found = shutil.which(variant.cli)
    if found:
        return found
    if sys.platform == "darwin":
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            bundled = apps / variant.app / "Contents/Resources/app/bin" / variant.cli
            if bundled.exists():
                return str(bundled)
    return None


def detect() -> list[Editor]:
    editors = []
    for variant in VARIANTS:
        candidates = variant.user_dirs()
        user_dir = next((d for d in candidates if d.exists()), candidates[0])
        cli = _find_cli(variant)
        if user_dir.exists() or cli or (Path.home() / variant.ext_home).exists():
            editors.append(Editor(variant, user_dir, cli))
    return editors
