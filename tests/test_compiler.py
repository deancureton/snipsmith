import textwrap

import pytest

from snipsmith.compiler import (
    ARTIFACTS,
    SnippetError,
    compile_all,
    escape_regex_slashes,
    load_source,
    lua_replacement_nodes,
    translate_capture_groups,
)


def test_committed_build_matches_fresh_compile(repo_root, repo_snippets):
    artifacts = compile_all(load_source(repo_snippets))
    for name, artifact in ARTIFACTS.items():
        expected = (repo_root / "build" / artifact.filename).read_text(encoding="utf-8")
        assert artifacts[name] == expected, artifact.filename


def write_yaml(tmp_path, body: str):
    path = tmp_path / "snippets.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_validation_errors_raise(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        snippets:
          - trigger: '([a-z])bar'
            replacement: "\\\\bar{[[3]]}"
            regex: true
          - trigger: bad
            replacement: x
            bogus: 1
    """,
    )
    with pytest.raises(SnippetError) as info:
        load_source(path)
    messages = "\n".join(info.value.errors)
    assert "only has 1 capture group" in messages
    assert "unknown key 'bogus'" in messages


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(SnippetError):
        load_source(tmp_path / "nope.yaml")


def test_platform_translations():
    assert translate_capture_groups("\\bar{[[0]]}") == "\\bar{``rv = m[1]``}"
    assert lua_replacement_nodes("\\bar{[[0]]}$1") == '{ t("\\\\bar{"), cap(1), t("}"), i(1) }'
    assert escape_regex_slashes(r"a/b\/c\\/d") == r"a\/b\/c\\\/d"


def test_generators_run_on_minimal_source(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        defaults:
          options:
            auto: true
        variables:
          GREEK: alpha|beta
        snippets:
          - trigger: '@({{GREEK}})'
            replacement: "\\\\[[0]]"
            regex: true
            options:
              math: true
          - trigger: mk
            replacement: $$1$
            options:
              text: true
    """,
    )
    artifacts = compile_all(load_source(path))
    assert "trigger: /@(${GREEK})/" in artifacts["obsidian_snippets"]
    assert '"${GREEK}": "alpha|beta"' in artifacts["obsidian_variables"]
    assert "snippet `@(alpha|beta)`" in artifacts["vscode"]
    assert 'trigEngine = "ecma"' in artifacts["neovim"]
