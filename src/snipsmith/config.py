"""user configuration: where the source yaml lives and where outputs go."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import tomllib

from .compiler import ARTIFACTS, PLATFORMS
from .errors import CliError
from .paths import xdg_config_home


def config_dir() -> Path:
    return xdg_config_home() / "snipsmith"


def config_file() -> Path:
    return config_dir() / "config.toml"


def default_snippets_file() -> Path:
    return config_dir() / "snippets.yaml"


def bundled_snippets_file() -> Path:
    """the starter snippets shipped in the wheel, or the repo copy in a dev checkout."""
    packaged = Path(__file__).parent / "data" / "snippets.yaml"
    return packaged if packaged.exists() else Path(__file__).parents[2] / "snippets.yaml"


def expand(path: str) -> Path:
    return Path(path).expanduser()


def collapse(path: Path) -> str:
    """render a path with ~ for the home directory."""
    home = Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _empty_outputs() -> dict[str, list[Path]]:
    return {name: [] for name in ARTIFACTS}


@dataclass
class Config:
    snippets: Path | None = None
    outputs: dict[str, list[Path]] = field(default_factory=_empty_outputs)

    def all_paths(self) -> list[Path]:
        return [path for paths in self.outputs.values() for path in paths]

    def has_destinations(self) -> bool:
        return bool(self.all_paths())

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_file()
        if not path.exists():
            return cls()
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise CliError(f"{path}: invalid config: {e}") from e
        outputs = {}
        for name, artifact in ARTIFACTS.items():
            values = (data.get(artifact.platform) or {}).get(artifact.key) or []
            if isinstance(values, str):
                values = [values]
            outputs[name] = [expand(v) for v in values]
        snippets = data.get("snippets")
        return cls(expand(snippets) if snippets else None, outputs)

    def dumps(self) -> str:
        def lst(paths: list[Path]) -> str:
            if not paths:
                return "[]"
            return "[\n" + "".join(f"    {json.dumps(collapse(p))},\n" for p in paths) + "]"

        sections = []
        if self.snippets:
            sections.append(f"snippets = {json.dumps(collapse(self.snippets))}\n")
        for platform in PLATFORMS:
            body = "\n".join(
                f"{artifact.key} = {lst(self.outputs[name])}"
                for name, artifact in ARTIFACTS.items()
                if artifact.platform == platform
            )
            sections.append(f"[{platform}]\n{body}\n")
        return "\n".join(sections)

    def save(self, path: Path | None = None) -> Path:
        path = path or config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dumps(), encoding="utf-8")
        return path
