"""detection and setup of the editor plugins snipsmith generates snippets for."""

from .. import ui
from . import neovim, obsidian, vscode

EDITORS = {"obsidian": obsidian, "vscode": vscode, "neovim": neovim}


def detect_all(wanted: set[str]) -> dict[str, list]:
    return {name: module.detect() if name in wanted else [] for name, module in EDITORS.items()}


def report(found: dict[str, list]) -> None:
    for name, targets in found.items():
        ui.heading(name)
        if not targets:
            ui.info(EDITORS[name].NOT_FOUND)
        for target in targets:
            detail = f"  {ui.dim(target.detail)}" if target.detail else ""
            ui.info(f"{target.name}{detail}  {target.status()}")
    print()
