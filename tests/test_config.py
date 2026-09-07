from pathlib import Path

from snipsmith.config import Config, bundled_snippets_file, collapse, config_file


def test_roundtrip(fake_home):
    cfg = Config(snippets=fake_home / "snippets.yaml")
    cfg.outputs["obsidian_snippets"].append(fake_home / "vault/s.js")
    cfg.outputs["obsidian_variables"].append(fake_home / "vault/v.json")
    cfg.outputs["vscode"] += [fake_home / "a.hsnips", Path("/abs/b.hsnips")]
    cfg.outputs["neovim"].append(fake_home / ".config/nvim/snipsmith/tex.lua")

    path = cfg.save()
    assert path == config_file() == fake_home / ".config/snipsmith/config.toml"
    text = path.read_text()
    assert '"~/vault/s.js"' in text
    assert '"/abs/b.hsnips"' in text
    assert "[obsidian]\nsnippets = [" in text
    assert Config.load() == cfg


def test_missing_config_is_empty(fake_home):
    cfg = Config.load()
    assert cfg == Config()
    assert not cfg.has_destinations()


def test_collapse(fake_home):
    assert collapse(fake_home) == "~"
    assert collapse(fake_home / "x/y") == "~/x/y"
    assert collapse(Path("/etc/hosts")) == "/etc/hosts"


def test_bundled_snippets_exist():
    assert bundled_snippets_file().exists()
