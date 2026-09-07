import json

import pytest

from snipsmith.editors import neovim, obsidian, vscode
from snipsmith.errors import EditorError


def make_vault(home):
    vault = home / "Notes"
    (vault / ".obsidian").mkdir(parents=True)
    (home / "Library/Application Support/obsidian").mkdir(parents=True)
    (home / "Library/Application Support/obsidian/obsidian.json").write_text(
        json.dumps({"vaults": {"a": {"path": str(vault)}, "b": {"path": str(home / "missing")}}})
    )
    return vault


def test_obsidian_detect_skips_missing_vaults(fake_home, monkeypatch):
    vault = make_vault(fake_home)
    monkeypatch.setattr(obsidian.sys, "platform", "darwin")
    found = obsidian.detect()
    assert [v.path for v in found] == [vault]
    assert not found[0].plugin_installed
    assert found[0].status() == "latex suite not installed"


def test_obsidian_install_enable_configure(fake_home):
    vault = obsidian.Vault(make_vault(fake_home))
    assets = {
        "main.js": b"// plugin",
        "manifest.json": json.dumps({"id": obsidian.PLUGIN_ID, "version": "9.9.9"}).encode(),
        "styles.css": b"",
    }
    assert (
        vault.install(fetch=lambda url: assets[url.rsplit("/", 1)[1]])
        == "installed latex suite 9.9.9"
    )
    assert vault.plugin_installed and vault.plugin_version == "9.9.9"

    assert vault.enable() is True
    assert vault.enable() is False
    assert vault.plugin_enabled and vault.community_plugins_enabled

    (vault.plugin_dir / "data.json").write_text(json.dumps({"snippetsTrigger": "Tab"}))
    vault.configure(vault.path / "snips/snippets.js", vault.path / "snips/variables.json")
    data = json.loads((vault.plugin_dir / "data.json").read_text())
    assert data["snippetsTrigger"] == "Tab"
    assert data["loadSnippetsFromFile"] is True
    assert data["snippetsFileLocation"] == "snips/snippets.js"
    assert data["snippetVariablesFileLocation"] == "snips/variables.json"
    assert vault.status() == "latex suite 9.9.9 enabled"
    assert vault.contains(vault.path / "x.js") and not vault.contains(fake_home / "x.js")


def test_obsidian_install_failure_is_an_editor_error(fake_home):
    vault = obsidian.Vault(make_vault(fake_home))

    def fetch(url):
        raise OSError("offline")

    with pytest.raises(EditorError, match="offline"):
        vault.install(fetch=fetch)


def test_vscode_settings_and_paths(fake_home, monkeypatch):
    monkeypatch.setattr(vscode.sys, "platform", "darwin")
    monkeypatch.setattr(vscode, "_find_cli", lambda variant: None)
    user = fake_home / "Library/Application Support/Code/User"
    user.mkdir(parents=True)
    user.joinpath("settings.json").write_text('{\n  // comment\n  "editor.fontSize": 14,\n}\n')
    (fake_home / ".vscode/extensions/draivin.hsnips-0.2.9").mkdir(parents=True)

    editors = vscode.detect()
    assert [e.name for e in editors] == ["Visual Studio Code"]
    editor = editors[0]
    assert editor.extension_installed
    assert editor.hsnips_dir == user / "globalStorage/draivin.hsnips/hsnips"
    with pytest.raises(EditorError, match="`code` command"):
        editor.install()

    user.joinpath("settings.json").write_text(json.dumps({"hsnips.hsnipsPath": "~/mysnips"}))
    assert editor.hsnips_dir == fake_home / "mysnips"


def test_neovim_detection_helpers(fake_home):
    config = fake_home / ".config/nvim"
    data = fake_home / ".local/share/nvim"
    luasnip = data / "site/pack/core/opt/LuaSnip"
    (luasnip / "lua/luasnip").mkdir(parents=True)
    (luasnip / "lua/luasnip/init.lua").write_text("")
    config.mkdir(parents=True)
    (config / "nvim-pack-lock.json").write_text("{}")

    assert neovim.find_luasnip(config, data) == luasnip
    assert neovim.detect_manager(config, data) == "vim.pack"
    assert "vim.pack.add" in neovim.install_hint("vim.pack")

    nvim = neovim.Neovim("0.11", config, data, luasnip, "vim.pack")
    assert not nvim.jsregexp_installed
    (luasnip / "deps").mkdir()
    (luasnip / "deps/luasnip-jsregexp.so").write_bytes(b"")
    assert nvim.jsregexp_installed

    loader = nvim.write_loader(config / "snipsmith")
    assert loader == config / "plugin/snipsmith.lua"
    assert nvim.loader_is_ours()
    assert f'local snippets_dir = "{config / "snipsmith"}"' in loader.read_text()


def test_neovim_detect_uses_xdg(fake_home, monkeypatch):
    monkeypatch.setattr(neovim.shutil, "which", lambda name: "/usr/bin/nvim")
    monkeypatch.setattr(neovim, "_version", lambda exe: "v0.11.0")
    (nvim,) = neovim.detect()
    assert nvim.config_dir == fake_home / ".config/nvim"
    assert nvim.data_dir == fake_home / ".local/share/nvim"
    assert nvim.luasnip_dir is None
    monkeypatch.setattr(neovim.shutil, "which", lambda name: None)
    assert neovim.detect() == []
