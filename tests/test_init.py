"""drive the interactive init flow with scripted answers."""

import json
import stat

import pytest

from snipsmith import init, ui
from snipsmith.config import Config
from snipsmith.editors import neovim, obsidian, vscode


class Prompter:
    """answers prompts in order; an empty string accepts the default."""

    def __init__(self, monkeypatch, answers):
        self.answers = list(answers)
        self.seen = []
        monkeypatch.setattr(ui, "accept_defaults", False)
        monkeypatch.setattr(
            ui.sys, "stdin", type("Tty", (), {"isatty": staticmethod(lambda: True)})()
        )
        monkeypatch.setattr("builtins.input", self)

    def __call__(self, prompt):
        self.seen.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def done(self):
        assert not self.answers, f"unused answers: {self.answers}"


@pytest.fixture
def prompter(monkeypatch):
    def make(*answers):
        return Prompter(monkeypatch, answers)

    return make


@pytest.fixture
def nothing_detected(monkeypatch):
    monkeypatch.setattr(obsidian, "detect", lambda: [])
    monkeypatch.setattr(vscode, "detect", lambda: [])
    monkeypatch.setattr(neovim, "detect", lambda: [])


def make_vault(home, name="Notes", plugin=False, enabled=False):
    vault = obsidian.Vault(home / name)
    vault.config_dir.mkdir(parents=True)
    if plugin:
        vault.plugin_dir.mkdir(parents=True)
        (vault.plugin_dir / "main.js").write_text("")
        (vault.plugin_dir / "manifest.json").write_text(json.dumps({"version": "1.0.0"}))
    if enabled:
        (vault.config_dir / "community-plugins.json").write_text(
            json.dumps(["other", obsidian.PLUGIN_ID])
        )
    return vault


# source file


def test_source_created_from_bundle_by_default(fake_home, nothing_detected, prompter):
    p = prompter("", "", "")  # path, create from bundle, no vault by path
    cfg = init.run(None, None)
    p.done()
    assert cfg.snippets == fake_home / ".config/snipsmith/snippets.yaml"
    assert "snippets:" in cfg.snippets.read_text()
    assert not cfg.has_destinations()
    assert Config.load() == cfg


def test_source_empty_file_and_directory_answer(fake_home, nothing_detected, prompter):
    target = fake_home / "mine"
    target.mkdir()
    p = prompter(str(target), "n", "y", "")
    cfg = init.run(None, None)
    p.done()
    assert cfg.snippets == target / "snippets.yaml"
    assert cfg.snippets.read_text().startswith("defaults:")


def test_source_reasks_when_both_declined(fake_home, nothing_detected, prompter):
    existing = fake_home / "real.yaml"
    existing.write_text("snippets: []\n")
    p = prompter("~/nope.yaml", "n", "n", "~/real.yaml", "")
    cfg = init.run(None, None)
    p.done()
    assert cfg.snippets == existing


def test_existing_config_prefills_source(fake_home, nothing_detected, prompter):
    existing = fake_home / "old.yaml"
    existing.write_text("snippets: []\n")
    Config(snippets=existing).save()
    p = prompter("", "")
    cfg = init.run(None, None)
    p.done()
    assert cfg.snippets == existing
    assert "~/old.yaml" in p.seen[0]


def test_yes_accepts_every_default(fake_home, nothing_detected, monkeypatch):
    monkeypatch.setattr(ui, "accept_defaults", True)
    cfg = init.run(None, None)
    assert cfg.snippets.exists()


# obsidian


def test_obsidian_manual_vault_and_custom_files(fake_home, nothing_detected, prompter):
    vault = make_vault(fake_home, plugin=True)
    outside = fake_home / "elsewhere.js"
    p = prompter(
        "",
        "",  # source
        "y",
        str(vault.path),  # add vault by path
        "",  # set up this vault
        str(outside),
        "snips/s.js",  # snippets: rejected outside, then accepted
        str(vault.path / "snips/v.json"),  # absolute inside vault
    )
    cfg = init.run(["obsidian"], None)
    p.done()
    assert cfg.outputs["obsidian_snippets"] == [vault.path / "snips/s.js"]
    assert cfg.outputs["obsidian_variables"] == [vault.path / "snips/v.json"]
    data = json.loads((vault.plugin_dir / "data.json").read_text())
    assert data["snippetsFileLocation"] == "snips/s.js"
    assert vault.plugin_enabled


def test_obsidian_decline_download_still_records_paths(fake_home, prompter, monkeypatch, capsys):
    vault = make_vault(fake_home)
    monkeypatch.setattr(obsidian, "detect", lambda: [vault])
    p = prompter("", "", "", "", "", "n")
    cfg = init.run(["obsidian"], None)
    p.done()
    assert cfg.outputs["obsidian_snippets"] == [vault.path / obsidian.DEFAULT_SNIPPETS]
    assert not vault.plugin_installed
    assert not (vault.plugin_dir / "data.json").exists()


def test_obsidian_download_failure_is_reported(fake_home, prompter, monkeypatch, capsys):
    vault = make_vault(fake_home)
    monkeypatch.setattr(obsidian, "detect", lambda: [vault])

    def fetch(url):
        raise OSError("no network")

    monkeypatch.setattr(obsidian, "_fetch", fetch)
    p = prompter("", "", "", "", "", "")
    init.run(["obsidian"], None)
    p.done()
    assert "no network" in capsys.readouterr().err


def test_obsidian_skip_one_vault_of_two(fake_home, prompter, monkeypatch, capsys):
    a = make_vault(fake_home, "A", plugin=True, enabled=True)
    b = make_vault(fake_home, "B", plugin=True)
    monkeypatch.setattr(obsidian, "detect", lambda: [a, b])
    monkeypatch.setattr(obsidian, "is_running", lambda: True)
    p = prompter("", "", "n", "", "", "")
    cfg = init.run(["obsidian"], None)
    p.done()
    assert cfg.outputs["obsidian_snippets"] == [b.path / obsidian.DEFAULT_SNIPPETS]
    assert not (a.plugin_dir / "data.json").exists()
    err = capsys.readouterr().err
    assert "restricted mode" in err
    assert "obsidian is running" in err


# vscode


def make_editor(fake_home, cli=None, installed=False):
    variant = vscode.VARIANTS[0]
    user_dir = fake_home / "Library/Application Support/Code/User"
    user_dir.mkdir(parents=True)
    if installed:
        (fake_home / ".vscode/extensions/draivin.hsnips-0.2.9").mkdir(parents=True)
    return vscode.Editor(variant, user_dir, cli)


def test_vscode_missing_cli_is_reported_and_files_customised(
    fake_home, prompter, monkeypatch, capsys
):
    editor = make_editor(fake_home)
    monkeypatch.setattr(vscode, "detect", lambda: [editor])
    p = prompter("", "", "", "", "~/snips", "latex, markdown.hsnips")
    cfg = init.run(["vscode"], None)
    p.done()
    assert "`code` command" in capsys.readouterr().err
    assert cfg.outputs["vscode"] == [
        fake_home / "snips/latex.hsnips",
        fake_home / "snips/markdown.hsnips",
    ]


def test_vscode_install_via_cli_and_decline_second_editor(
    fake_home, prompter, monkeypatch, tmp_path
):
    fake_cli = tmp_path / "code"
    fake_cli.write_text('#!/bin/sh\necho "$@" > "$0.log"\n')
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    first = make_editor(fake_home, cli=str(fake_cli))
    second = vscode.Editor(
        vscode.VARIANTS[2], fake_home / "Library/Application Support/Cursor/User", None
    )
    monkeypatch.setattr(vscode, "detect", lambda: [first, second])
    p = prompter("", "", "", "", "", "", "n")
    cfg = init.run(["vscode"], None)
    p.done()
    assert "--install-extension draivin.hsnips" in (tmp_path / "code.log").read_text()
    assert cfg.outputs["vscode"] == [first.hsnips_dir / name for name in vscode.DEFAULT_FILES]


# neovim


def make_nvim(fake_home, luasnip=False, jsregexp=False, manager="lazy.nvim"):
    config = fake_home / ".config/nvim"
    data = fake_home / ".local/share/nvim"
    config.mkdir(parents=True)
    luasnip_dir = None
    if luasnip:
        luasnip_dir = data / "lazy/LuaSnip"
        (luasnip_dir / "lua/luasnip").mkdir(parents=True)
        if jsregexp:
            (luasnip_dir / "deps").mkdir()
            (luasnip_dir / "deps/luasnip-jsregexp.so").write_bytes(b"")
    return neovim.Neovim("v0.11.0", config, data, luasnip_dir, manager)


def test_neovim_without_luasnip_prints_hint_and_writes_loader(
    fake_home, prompter, monkeypatch, capsys
):
    nvim = make_nvim(fake_home)
    monkeypatch.setattr(neovim, "detect", lambda: [nvim])
    p = prompter("", "", "", "", "")
    cfg = init.run(["neovim"], None)
    p.done()
    out = capsys.readouterr()
    assert "lazy.nvim plugin specs" in out.out
    assert nvim.loader_is_ours()
    assert cfg.outputs["neovim"] == [nvim.default_snippets_dir / n for n in neovim.DEFAULT_FILES]


def test_neovim_jsregexp_build_without_toolchain(fake_home, prompter, monkeypatch, capsys):
    nvim = make_nvim(fake_home, luasnip=True)
    monkeypatch.setattr(neovim, "detect", lambda: [nvim])
    monkeypatch.setattr(neovim.shutil, "which", lambda name: None)
    p = prompter("", "", "", "", "", "")
    init.run(["neovim"], None)
    p.done()
    assert "c toolchain" in capsys.readouterr().err


def test_neovim_foreign_loader_is_not_overwritten(fake_home, prompter, monkeypatch, capsys):
    nvim = make_nvim(fake_home, luasnip=True, jsregexp=True)
    nvim.loader_file.parent.mkdir(parents=True)
    nvim.loader_file.write_text("-- mine\n")
    monkeypatch.setattr(neovim, "detect", lambda: [nvim])
    p = prompter("", "", "", "~/.config/nvim/snips", "", "n")
    cfg = init.run(["neovim"], None)
    p.done()
    assert nvim.loader_file.read_text() == "-- mine\n"
    assert cfg.outputs["neovim"][0] == fake_home / ".config/nvim/snips/tex.lua"
    assert "from_lua loader" in capsys.readouterr().out


def test_neovim_own_loader_is_rewritten_silently(fake_home, prompter, monkeypatch):
    nvim = make_nvim(fake_home, luasnip=True, jsregexp=True)
    nvim.write_loader(fake_home / "old")
    monkeypatch.setattr(neovim, "detect", lambda: [nvim])
    p = prompter("", "", "", "", "")
    init.run(["neovim"], None)
    p.done()
    assert str(nvim.default_snippets_dir) in nvim.loader_file.read_text()


def test_skip_and_only_filters(fake_home, prompter, monkeypatch):
    calls = []
    for module in (obsidian, vscode, neovim):
        monkeypatch.setattr(module, "detect", lambda m=module: calls.append(m.__name__) or [])
    p = prompter("", "")
    init.run(None, ["obsidian", "neovim"])
    p.done()
    assert calls == ["snipsmith.editors.vscode"]


def test_invalid_answer_reprompts(fake_home, nothing_detected, prompter, capsys):
    p = prompter("", "maybe", "y", "")
    init.run(None, None)
    p.done()
    assert "please answer y or n" in capsys.readouterr().out
