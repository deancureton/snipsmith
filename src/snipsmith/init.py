"""the interactive `snipsmith init` flow."""

from collections.abc import Callable
from pathlib import Path

from . import editors, ui
from .compiler import EMPTY_SOURCE, PLATFORMS
from .config import (
    Config,
    bundled_snippets_file,
    collapse,
    config_file,
    default_snippets_file,
    expand,
)
from .editors import neovim, obsidian, vscode
from .errors import EditorError


def ask_path(question: str, default: Path) -> Path:
    return expand(ui.ask(question, collapse(default))).resolve()


def ask_files(default_names: tuple[str, ...], suffix: str) -> list[str]:
    answer = ui.ask("  files to write", ", ".join(default_names))
    names = [name.strip() for name in answer.split(",") if name.strip()]
    return [name if name.endswith(suffix) else name + suffix for name in names]


def attempt(action: Callable[[], str]) -> None:
    """run an install step, reporting success or the editor's explanation of failure."""
    try:
        ui.ok(f"  {action()}")
    except EditorError as e:
        ui.error(f"  {e}")


def setup_source(existing: Config) -> Path:
    default = existing.snippets or default_snippets_file()
    while True:
        path = ask_path("where should your snippets.yaml live?", default)
        if path.is_dir():
            path = path / "snippets.yaml"
        if path.exists():
            ui.ok(f"using existing {collapse(path)}")
            return path
        if ui.confirm(
            f"{collapse(path)} does not exist; create it from the bundled starter snippets?"
        ):
            content = bundled_snippets_file().read_text(encoding="utf-8")
        elif ui.confirm("start from an empty file instead?", default=False):
            content = EMPTY_SOURCE
        else:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ui.ok(f"created {collapse(path)}")
        return path


def existing_in_vault(existing: Config, name: str, vault: obsidian.Vault) -> str | None:
    """the vault-relative name of an already-configured output inside this vault."""
    return next((vault.relative(p) for p in existing.outputs[name] if vault.contains(p)), None)


def ask_vault_file(vault: obsidian.Vault, label: str, default_name: str) -> Path:
    while True:
        path = expand(ui.ask(f"  {label} (relative to the vault)", default_name))
        if not path.is_absolute():
            path = vault.path / path
        if vault.contains(path):
            return path.resolve()
        ui.warn("  the file has to live inside the vault so obsidian can read it")


def setup_obsidian(vaults: list[obsidian.Vault], cfg: Config, existing: Config) -> None:
    if not vaults:
        if not ui.confirm("obsidian: no vaults were detected; add a vault by path?", default=False):
            return
        vaults = [obsidian.Vault(ask_path("vault path", Path.home()))]

    configured = False
    for vault in vaults:
        if not ui.confirm(f"set up latex suite in vault {ui.bold(vault.name)}?"):
            continue
        snippets_file = ask_vault_file(
            vault,
            "snippets file",
            existing_in_vault(existing, "obsidian_snippets", vault) or obsidian.DEFAULT_SNIPPETS,
        )
        variables_file = ask_vault_file(
            vault,
            "variables file",
            existing_in_vault(existing, "obsidian_variables", vault) or obsidian.DEFAULT_VARIABLES,
        )
        if not vault.plugin_installed and ui.confirm(
            "  latex suite is not installed; download the latest release into this vault?"
        ):
            attempt(vault.install)
        if vault.plugin_installed:
            restricted = not vault.community_plugins_enabled
            if vault.enable():
                ui.ok("  enabled latex suite")
            vault.configure(snippets_file, variables_file)
            ui.ok("  pointed latex suite at the generated files")
            configured = True
            if restricted:
                ui.warn(
                    "  community plugins look disabled; turn off restricted mode in "
                    "settings → community plugins"
                )
        cfg.outputs["obsidian_snippets"].append(snippets_file)
        cfg.outputs["obsidian_variables"].append(variables_file)

    if configured and obsidian.is_running():
        ui.warn("obsidian is running; restart it so latex suite picks up the new settings")


def setup_vscode(found: list[vscode.Editor], cfg: Config, existing: Config) -> None:
    if not found:
        ui.info("vscode: no editors detected, skipping")
        return
    for editor in found:
        if not ui.confirm(f"set up hypersnips in {ui.bold(editor.name)}?"):
            continue
        if not editor.extension_installed and ui.confirm(
            "  hypersnips is not installed; install it now?"
        ):
            attempt(editor.install)
        hsnips_dir = ask_path("  hsnips snippets directory", editor.hsnips_dir)
        for name in ask_files(vscode.DEFAULT_FILES, vscode.SUFFIX):
            cfg.outputs["vscode"].append(hsnips_dir / name)
        ui.ok(f"  writing to {collapse(hsnips_dir)}")


def setup_neovim(found: list[neovim.Neovim], cfg: Config, existing: Config) -> None:
    if not found:
        ui.info("neovim: nvim not found, skipping")
        return
    nvim = found[0]
    if not ui.confirm(f"set up luasnip in {ui.bold('neovim')}?"):
        return

    if nvim.luasnip_dir is None:
        ui.warn("  luasnip is not installed; snipsmith will still write snippet files and a loader")
        print("  " + neovim.install_hint(nvim.manager).replace("\n", "\n  "))
    elif not nvim.jsregexp_installed:
        ui.warn("  jsregexp is not built, so regex-triggered snippets would not expand")
        if ui.confirm("  build it now with `make install_jsregexp`?"):
            attempt(nvim.install)

    previous = existing.outputs["neovim"]
    snippets_dir = ask_path(
        "  directory for the generated lua snippet files",
        previous[0].parent if previous else nvim.default_snippets_dir,
    )
    for name in ask_files(neovim.DEFAULT_FILES, neovim.SUFFIX):
        cfg.outputs["neovim"].append(snippets_dir / name)

    loader = nvim.loader_file
    foreign = loader.exists() and not nvim.loader_is_ours()
    question = (
        f"  {collapse(loader)} exists and was not written by snipsmith; overwrite it?"
        if foreign
        else f"  write {collapse(loader)} to load them? (say no if your config already loads that directory)"
    )
    if not ui.confirm(question, default=not foreign):
        if foreign:
            ui.info("  add the snippets directory to your own luasnip from_lua loader instead")
        return
    nvim.write_loader(snippets_dir)
    ui.ok(f"  wrote loader {collapse(loader)}")


SETUP = {"obsidian": setup_obsidian, "vscode": setup_vscode, "neovim": setup_neovim}


def run(only: list[str] | None, skip: list[str] | None) -> Config:
    wanted = set(only or PLATFORMS) - set(skip or [])
    existing = Config.load()
    if config_file().exists():
        ui.info(f"existing config at {collapse(config_file())} will be replaced\n")

    ui.heading("detecting editors…\n")
    found = editors.detect_all(wanted)
    editors.report(found)

    cfg = Config(snippets=setup_source(existing))
    print()
    for platform in PLATFORMS:
        if platform in wanted:
            SETUP[platform](found[platform], cfg, existing)
            print()

    ui.ok(f"saved config to {collapse(cfg.save())}")
    return cfg
