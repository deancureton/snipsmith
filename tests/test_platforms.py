"""detection under linux and windows directory layouts."""

import json
from pathlib import Path

from snipsmith import paths
from snipsmith.editors import neovim, obsidian, vscode


def test_linux_layouts(fake_home, monkeypatch):
    for module in (paths, obsidian, vscode, neovim):
        monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(vscode, "_find_cli", lambda variant: None)

    vault = fake_home / "notes"
    vault.mkdir()
    for cfg in (
        fake_home / ".config/obsidian/obsidian.json",
        fake_home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",
    ):
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"vaults": {"x": {"path": str(vault)}}}))
    assert [v.path for v in obsidian.detect()] == [vault]

    flatpak_user = fake_home / ".var/app/com.visualstudio.code/config/Code/User"
    flatpak_user.mkdir(parents=True)
    (fake_home / ".config/Cursor/User").mkdir(parents=True)
    editors = {e.name: e for e in vscode.detect()}
    assert set(editors) == {"Visual Studio Code", "Cursor"}
    assert editors["Visual Studio Code"].user_dir == flatpak_user
    assert (
        editors["Cursor"].hsnips_dir
        == fake_home / ".config/Cursor/User/globalStorage/draivin.hsnips/hsnips"
    )

    monkeypatch.setattr(neovim.shutil, "which", lambda name: "/usr/bin/nvim")
    monkeypatch.setattr(neovim, "_version", lambda exe: "v0.10.0")
    monkeypatch.setenv("NVIM_APPNAME", "nvim-custom")
    (nvim,) = neovim.detect()
    assert nvim.config_dir == fake_home / ".config/nvim-custom"
    assert nvim.data_dir == fake_home / ".local/share/nvim-custom"


def test_windows_layouts(fake_home, monkeypatch):
    for module in (paths, obsidian, vscode, neovim):
        monkeypatch.setattr(module.sys, "platform", "win32")
    appdata = fake_home / "AppData/Roaming"
    local = fake_home / "AppData/Local"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(vscode, "_find_cli", lambda variant: None)

    vault = fake_home / "Documents/notes"
    vault.mkdir(parents=True)
    (appdata / "obsidian").mkdir(parents=True)
    (appdata / "obsidian/obsidian.json").write_text(
        json.dumps({"vaults": {"x": {"path": str(vault)}}})
    )
    assert [v.path for v in obsidian.detect()] == [vault]

    (appdata / "Code/User").mkdir(parents=True)
    (editor,) = vscode.detect()
    assert editor.hsnips_dir == appdata / "Code/User/globalStorage/draivin.hsnips/hsnips"

    monkeypatch.setattr(neovim.shutil, "which", lambda name: "C:\\nvim\\nvim.exe")
    monkeypatch.setattr(neovim, "_version", lambda exe: "v0.10.0")
    (nvim,) = neovim.detect()
    assert nvim.config_dir == local / "nvim"
    assert nvim.data_dir == local / "nvim-data"


def test_corrupt_obsidian_json_is_ignored(fake_home, monkeypatch):
    monkeypatch.setattr(obsidian.sys, "platform", "darwin")
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    cfg = fake_home / "Library/Application Support/obsidian/obsidian.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json")
    assert obsidian.detect() == []


def test_app_data_root_falls_back_to_xdg(fake_home, monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.app_data_root() == fake_home / ".config"
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    assert paths.app_data_root() == Path(fake_home / ".config")
