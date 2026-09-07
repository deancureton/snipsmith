"""turn compiled artifacts into files on disk."""

from dataclasses import dataclass
import difflib
from pathlib import Path

from .compiler import ARTIFACTS, SnippetSource, compile_all
from .config import Config
from .errors import CliError


@dataclass
class Planned:
    path: Path
    content: str
    current: str | None

    @property
    def changed(self) -> bool:
        return self.current != self.content


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def plan(source: SnippetSource, cfg: Config, out_dir: Path | None = None) -> list[Planned]:
    """every destination with the content it should hold."""
    artifacts = compile_all(source)
    targets: dict[Path, str] = {}
    for name, paths in cfg.outputs.items():
        for path in paths:
            targets[path] = artifacts[name]
    if out_dir:
        for name, artifact in ARTIFACTS.items():
            targets[out_dir / artifact.filename] = artifacts[name]
    if not targets:
        raise CliError("no output paths configured; run `snipsmith init` or pass --out DIR")
    return [Planned(path, content, _read(path)) for path, content in targets.items()]


def write(planned: Planned) -> None:
    """
    temp file + rename, so a reader never sees a truncated snippet file. writes
    through symlinks (dotfiles setups) instead of replacing them.
    """
    path = planned.path.resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(planned.content, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        raise CliError(f"could not write {planned.path}: {e.strerror or e}") from e


def render_diff(planned: list[Planned]) -> str:
    chunks = []
    for item in planned:
        if not item.changed:
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    (item.current or "").splitlines(keepends=True),
                    item.content.splitlines(keepends=True),
                    fromfile=str(item.path),
                    tofile=f"{item.path} (new)",
                )
            )
        )
    return "\n".join(chunks)
