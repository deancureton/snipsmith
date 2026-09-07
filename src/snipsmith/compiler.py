"""compile snippets.yaml into latex suite, hsnips, and luasnip snippet files."""

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

import yaml

ALLOWED_SNIPPET_KEYS = {
    "trigger",
    "regex",
    "replacement",
    "description",
    "target_platforms",
    "priority",
    "options",
    "platforms",
    "excluded_macros",
}
ALLOWED_OVERRIDE_KEYS = ALLOWED_SNIPPET_KEYS - {"target_platforms", "platforms"}
CONTEXT_KEYS = ("math", "inline_math", "display_math", "text", "code")
ALLOWED_OPTION_KEYS = {
    *CONTEXT_KEYS,
    "auto",
    "in_word",
    "word_boundary",
    "beginning_of_line",
    "multi_line",
}
IGNORED_CONTEXTS = {
    "vscode": {"inline_math", "display_math", "code"},
    "neovim": {"code"},
}

CAPTURE_GROUP_RE = re.compile(r"\[\[(\d+)\]\]")
VARIABLE_RE = re.compile(r"\{\{(\w+)\}\}")
HSNIPS_MATCH_REF_RE = re.compile(r"\bm\[(\d+)\]")
NEOVIM_TOKEN_RE = re.compile(
    CAPTURE_GROUP_RE.pattern + r"|\$\{(\d+):([^}]*)\}|\$(\d+)|\$\{VISUAL\}"
)

YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

EMPTY_SOURCE = "defaults:\n  options:\n    auto: true\n\nvariables: {}\n\nsnippets: []\n"


class SnippetError(Exception):
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        super().__init__("\n".join(errors))
        self.errors = errors
        self.warnings = warnings or []


@dataclass
class SnippetSource:
    snippets: list[dict[str, Any]]
    variables: dict[str, str]
    verbatim_snippets: dict[str, list[str]]
    default_options: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def merge_for_platform(
    snippet: dict[str, Any], platform: str, default_options: dict[str, Any]
) -> dict[str, Any]:
    """precedence, lowest to highest: defaults < snippet < platform override."""
    override = (snippet.get("platforms") or {}).get(platform) or {}
    merged = {**snippet, **override}
    merged["options"] = {
        **default_options,
        **(snippet.get("options") or {}),
        **(override.get("options") or {}),
    }
    return merged


def targets_platform(snippet: dict[str, Any], platform: str) -> bool:
    target_platforms = snippet.get("target_platforms")
    return not target_platforms or platform in target_platforms


def resolved(source: SnippetSource, platform: str):
    """yield each snippet that targets the platform, merged for it."""
    for snippet in source.snippets:
        if targets_platform(snippet, platform):
            yield merge_for_platform(snippet, platform, source.default_options)


def substitute_variables(text: str, variables: dict[str, str]) -> str:
    for var, val in variables.items():
        text = text.replace(f"{{{{{var}}}}}", val)
    return text


def escape_regex_slashes(pattern: str) -> str:
    """escape unescaped `/` so the pattern can sit inside a javascript /.../ literal."""
    return re.sub(r"\\.|/", lambda m: "\\/" if m[0] == "/" else m[0], pattern, flags=re.S)


def translate_capture_groups(replacement: str) -> str:
    """obsidian's [[n]] is hsnips' m[n+1]."""
    return CAPTURE_GROUP_RE.sub(lambda m: f"``rv = m[{int(m.group(1)) + 1}]``", replacement)


def lua_quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def iter_neovim_tokens(replacement: str):
    """yield (start, end, kind, n, default) for each [[n]], $n/${n:default}, or ${VISUAL}."""
    for m in NEOVIM_TOKEN_RE.finditer(replacement):
        if m.group(1) is not None:
            yield m.start(), m.end(), "capture", int(m.group(1)), None
        elif m.group(0) == "${VISUAL}":
            yield m.start(), m.end(), "visual", None, None
        else:
            yield m.start(), m.end(), "tabstop", int(m.group(2) or m.group(4)), m.group(3)


def _append_lua_text(nodes: list[str], text: str) -> None:
    if not text:
        return
    lines = text.split("\n")
    if len(lines) == 1:
        nodes.append(f"t({lua_quote(text)})")
    else:
        nodes.append("t({ " + ", ".join(lua_quote(line) for line in lines) + " })")


def lua_replacement_nodes(replacement: str) -> str:
    """translate literals, $n tabstops, and [[n]] captures into a luasnip node list."""
    nodes: list[str] = []
    seen_stops = set()
    pos = 0
    for start, end, kind, n, default in iter_neovim_tokens(replacement):
        _append_lua_text(nodes, replacement[pos:start])
        pos = end
        if kind == "capture":
            nodes.append(f"cap({n + 1})")
        elif kind == "visual":
            nodes.append("vis()")
        elif n in seen_stops:
            nodes.append(f"rep({n})")
        else:
            seen_stops.add(n)
            nodes.append(f"i({n})" if default is None else f"i({n}, {lua_quote(default)})")
    _append_lua_text(nodes, replacement[pos:])
    if not nodes:
        nodes.append('t("")')
    return "{ " + ", ".join(nodes) + " }"


def matches_in_word(options: dict[str, Any]) -> bool:
    return options.get("in_word", True) and not options.get("word_boundary")


def emission_order(entries: list[tuple[int, str]]) -> list[str]:
    """
    longest trigger first; hsnips and luasnip both fall back to definition order on
    priority ties, so this reproduces obsidian's tie-breaking on every platform.
    """
    return [text for _, text in sorted(entries, key=lambda e: -e[0])]


# validation


def _check_vscode(merged, trigger, is_regex, group_count, where) -> tuple[list[str], list[str]]:
    errors = []
    replacement = merged["replacement"]
    if is_regex:
        for n in HSNIPS_MATCH_REF_RE.findall(replacement):
            if int(n) > group_count:
                errors.append(
                    f"{where}: replacement references m[{n}] but the trigger only "
                    f"has {group_count} capture group(s)"
                )
        # hsnips has no escaping mechanism for these characters
        if "`" in trigger:
            errors.append(f"{where}: backtick in regex trigger would corrupt the hsnips file")
    if '"' in merged.get("description", ""):
        errors.append(f"{where}: double quote in description would corrupt the hsnips file")
    if "${VISUAL}" in replacement:
        errors.append(
            f"{where}: ${{VISUAL}} is not supported for vscode; exclude vscode via target_platforms"
        )
    return errors, []


def _check_obsidian(merged, trigger, is_regex, group_count, where) -> tuple[list[str], list[str]]:
    if not is_regex and "\n" in trigger:
        return [f"{where}: obsidian triggers may not contain newlines"], []
    return [], []


def _check_neovim(merged, trigger, is_regex, group_count, where) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    replacement = merged["replacement"]
    if "``" in replacement:
        errors.append(
            f"{where}: hsnips javascript (``...``) in replacement is not supported for "
            f"neovim; add a platforms.neovim override or exclude neovim via target_platforms"
        )
    stops = [
        (start, n, default)
        for start, _, kind, n, default in iter_neovim_tokens(replacement)
        if kind == "tabstop"
    ]
    zero_positions = [p for p, n, _ in stops if n == 0]
    if len(zero_positions) > 1:
        errors.append(
            f"{where}: $0 appears more than once; luasnip cannot mirror the final tabstop"
        )
    if any(n == 0 and d is not None for _, n, d in stops):
        errors.append(
            f"{where}: ${{0:default}} is not supported for neovim ($0 is the final cursor position)"
        )
    if zero_positions and any(n != 0 and p > zero_positions[0] for p, n, _ in stops):
        warnings.append(
            f"{where}: $0 appears before other tabstops; luasnip visits $0 last (unlike "
            f"obsidian, which visits it first), so add a platforms.neovim override if the "
            f"jump order matters"
        )
    for _, _n, default in stops:
        if default and ("$" in default or "{" in default or CAPTURE_GROUP_RE.search(default)):
            errors.append(
                f"{where}: tabstop default {default!r} is too complex for the neovim "
                f"generator (no nested tabstops, captures, or braces)"
            )
    if not is_regex and "\n" in trigger:
        errors.append(f"{where}: neovim triggers may not contain newlines")
    return errors, warnings


PLATFORM_CHECKS = {
    "obsidian": _check_obsidian,
    "vscode": _check_vscode,
    "neovim": _check_neovim,
}


def validate(
    snippets: list[dict[str, Any]],
    variables: dict[str, str],
    default_options: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """returns (errors, warnings); errors abort the build."""
    errors: list[str] = []
    warnings: list[str] = []
    # (platform, trigger, is_regex, context, priority, replacement) -> first index seen
    seen: dict[tuple, int] = {}
    # snippet content ignoring target_platforms -> first index seen
    seen_content: dict[str, int] = {}

    def check_variable_refs(text: str, where: str) -> None:
        for var in VARIABLE_RE.findall(text):
            if var not in variables:
                errors.append(f"{where}: unknown variable {{{{{var}}}}}")

    def check_option_keys(options: dict[str, Any] | None, where: str) -> None:
        for key in options or {}:
            if key not in ALLOWED_OPTION_KEYS:
                errors.append(f"{where}: unknown option '{key}'")

    for i, snippet in enumerate(snippets):
        trigger = snippet.get("trigger")
        where = f"snippet #{i + 1} ({trigger!r})"

        if not isinstance(trigger, str) or not trigger:
            errors.append(f"{where}: 'trigger' must be a non-empty string")
            continue
        if not isinstance(snippet.get("replacement"), str):
            errors.append(f"{where}: 'replacement' must be a string")
            continue

        for key in snippet:
            if key not in ALLOWED_SNIPPET_KEYS:
                errors.append(f"{where}: unknown key '{key}'")
        check_option_keys(snippet.get("options"), where)

        content_key = json.dumps(
            {k: v for k, v in snippet.items() if k != "target_platforms"},
            sort_keys=True,
            default=str,
        )
        if content_key in seen_content:
            warnings.append(
                f"{where}: identical to snippet #{seen_content[content_key]} except for "
                f"target_platforms; merge them into one entry"
            )
        else:
            seen_content[content_key] = i + 1

        for platform in snippet.get("target_platforms") or []:
            if platform not in PLATFORMS:
                errors.append(f"{where}: unknown target platform '{platform}'")

        excluded = snippet.get("excluded_macros")
        if excluded is not None and (
            not isinstance(excluded, list) or not all(isinstance(m, str) for m in excluded)
        ):
            errors.append(f"{where}: excluded_macros must be a list of strings")

        for platform, override in (snippet.get("platforms") or {}).items():
            if platform not in PLATFORMS:
                errors.append(f"{where}: unknown override platform '{platform}'")
                continue
            for key in override or {}:
                if key not in ALLOWED_OVERRIDE_KEYS:
                    errors.append(f"{where}: platform override may not set '{key}'")
            check_option_keys((override or {}).get("options"), where)

        check_variable_refs(trigger, where)
        check_variable_refs(snippet["replacement"], where)

        for platform in PLATFORMS:
            if not targets_platform(snippet, platform):
                continue
            merged = merge_for_platform(snippet, platform, default_options)
            options = merged["options"]
            replacement = merged["replacement"]
            merged_trigger = substitute_variables(merged["trigger"], variables)
            is_regex = bool(merged.get("regex"))

            group_count = 0
            if is_regex:
                try:
                    group_count = re.compile(merged_trigger).groups
                except re.error as e:
                    errors.append(f"{where}: invalid regex trigger: {e}")
                    continue
                for n in CAPTURE_GROUP_RE.findall(replacement):
                    if int(n) + 1 > group_count:
                        errors.append(
                            f"{where}: replacement references [[{n}]] but the trigger only "
                            f"has {group_count} capture group(s)"
                        )
            elif CAPTURE_GROUP_RE.search(replacement):
                warnings.append(f"{where}: replacement uses [[n]] but the trigger is not a regex")

            platform_errors, platform_warnings = PLATFORM_CHECKS[platform](
                merged, merged_trigger, is_regex, group_count, where
            )
            errors.extend(platform_errors)
            warnings.extend(platform_warnings)

            ignored = IGNORED_CONTEXTS.get(platform)
            if (
                ignored
                and any(options.get(k) for k in ignored)
                and not any(options.get(k) for k in set(CONTEXT_KEYS) - ignored)
            ):
                warnings.append(
                    f"{where}: scoped only by options the {platform} builder ignores "
                    f"({', '.join(sorted(ignored))}); it will fire unscoped in {platform} "
                    f"unless target_platforms excludes it"
                )

            context = tuple(bool(options.get(k)) for k in CONTEXT_KEYS)
            key = (
                platform,
                merged_trigger,
                is_regex,
                context,
                merged.get("priority", 0),
                replacement,
            )
            if key in seen:
                warnings.append(
                    f"{where}: duplicate of snippet #{seen[key]} for {platform} (same "
                    f"trigger, context, priority, and replacement)"
                )
            else:
                seen[key] = i + 1

    return errors, warnings


# generators (pure: source in, file content out)

OBSIDIAN_FLAGS = (
    ("math", "m"),
    ("inline_math", "n"),
    ("display_math", "M"),
    ("text", "t"),
    ("code", "c"),
    ("auto", "A"),
)


def obsidian_flags(merged: dict[str, Any]) -> str:
    options = merged["options"]
    flags = "r" if merged.get("regex") else ""
    flags += "".join(flag for key, flag in OBSIDIAN_FLAGS if options.get(key))
    if not matches_in_word(options):
        flags += "w"
    return flags


def generate_obsidian_snippets(source: SnippetSource) -> str:
    entries = []
    for snippet in resolved(source, "obsidian"):
        # {{VAR}} becomes obsidian's native ${VAR}, substituted from the variables file
        trigger = VARIABLE_RE.sub(r"${\1}", snippet["trigger"])
        if snippet.get("regex"):
            trigger_str = f"trigger: /{escape_regex_slashes(trigger)}/"
        else:
            trigger_str = f"trigger: {json.dumps(trigger)}"

        parts = [
            trigger_str,
            f"replacement: {json.dumps(snippet['replacement'])}",
            f"options: {json.dumps(obsidian_flags(snippet))}",
            f"description: {json.dumps(snippet.get('description', ''))}",
        ]
        if "priority" in snippet:
            parts.append(f"priority: {snippet['priority']}")
        if snippet.get("excluded_macros"):
            parts.append(f"excludedMacros: {json.dumps(snippet['excluded_macros'])}")
        entries.append(f"    {{ {', '.join(parts)} }}")

    entries += [f"    {s.strip()}" for s in source.verbatim_snippets.get("obsidian", [])]
    return "[\n" + ",\n".join(entries) + "\n]\n"


def generate_obsidian_variables(source: SnippetSource) -> str:
    return json.dumps({f"${{{k}}}": v for k, v in source.variables.items()}, indent=4)


# math() also matches vscode's markdown math scopes; notmath() stays quiet inside
# code (markdown fenced/inline code, latex verbatim)
HSNIPS_PRELUDE = """\
global
function math(context) {
    return context.scopes.findLastIndex(s => s.startsWith("meta.math") || s.startsWith("meta.embedded.math")) > context.scopes.findLastIndex(s => s.startsWith("comment") || s.startsWith("meta.text.normal.tex"));
}
function notmath(context) {
    return !math(context) && !context.scopes.some(s => s.startsWith("markup.fenced_code") || s.startsWith("markup.raw") || s.startsWith("markup.inline.raw"));
}
endglobal

"""

VSCODE_FLAGS = (("word_boundary", "w"), ("beginning_of_line", "b"), ("multi_line", "M"))


def vscode_flags(options: dict[str, Any]) -> str:
    flags = "A" if options.get("auto") else ""
    flags += "i" if matches_in_word(options) else ""
    return flags + "".join(flag for key, flag in VSCODE_FLAGS if options.get(key))


def generate_vscode_snippets(source: SnippetSource) -> str:
    entries: list[tuple[int, str]] = []
    for snippet in resolved(source, "vscode"):
        options = snippet["options"]
        trigger = substitute_variables(snippet["trigger"], source.variables)
        replacement = substitute_variables(snippet["replacement"], source.variables)
        # a no-op for snippets that provide an explicit vscode replacement in hsnips syntax
        replacement = translate_capture_groups(replacement).replace("\\", "\\\\")

        trigger_length = len(trigger)
        is_regex = bool(snippet.get("regex"))
        # hsnips plaintext triggers cannot contain spaces
        if not is_regex and " " in trigger:
            trigger = re.escape(trigger)
            is_regex = True
        header = f"`{trigger}`" if is_regex else trigger

        lines = []
        if "priority" in snippet:
            lines.append(f"priority {snippet['priority']}")
        if options.get("math"):
            lines.append("context math(context)")
        elif options.get("text"):
            lines.append("context notmath(context)")
        lines += [
            f'snippet {header} "{snippet.get("description", "")}" {vscode_flags(options)}',
            replacement,
            "endsnippet",
        ]
        entries.append((trigger_length, "\n".join(lines) + "\n\n"))

    content = HSNIPS_PRELUDE + "".join(emission_order(entries))
    for verbatim in source.verbatim_snippets.get("vscode", []):
        content += f"{verbatim.strip()}\n\n"
    return content


NEOVIM_PRELUDE = """\
-- generated by snipsmith; do not edit by hand.

local ls = require("luasnip")
local s = ls.snippet
local t = ls.text_node
local i = ls.insert_node
local f = ls.function_node
local rep = require("luasnip.extras").rep
local line_begin = require("luasnip.extras.expand_conditions").line_begin

-- without jsregexp, ecma triggers silently degrade to plain-text matching;
-- luasnip's wrapper returns false (not an error) when it is missing
local jsregexp_ok, jsregexp = pcall(require, "luasnip.util.jsregexp")
if not (jsregexp_ok and jsregexp) then
  vim.schedule(function()
    vim.notify(
      "snipsmith: jsregexp is not installed; regex-triggered snippets will not expand."
        .. " see luasnip's `install_jsregexp` docs.",
      vim.log.levels.WARN
    )
  end)
end

local MATH_NODES = {
  math_environment = "display",
  inline_formula = "inline",
  displayed_equation = "display",
}

-- a half-typed group ("$lr{$") can break the latex parse and swallow the
-- math node into an ERROR; fall back to scanning for delimiters
local function delimiter_zone(line_to_cursor)
  local line = line_to_cursor:gsub("\\\\%$", "")
  local function last(pat)
    return line:match(".*()" .. pat) or 0
  end
  if last("\\\\%(") > last("\\\\%)") then
    return "inline"
  end
  if last("\\\\%[") > last("\\\\%]") then
    return "display"
  end
  local _, dbl = line:gsub("%$%$", "")
  local _, dollars = line:gsub("%$", "")
  if dbl % 2 == 1 then
    return "display"
  end
  if (dollars - 2 * dbl) % 2 == 1 then
    return "inline"
  end
end

-- "inline", "display", or nil; vimtex when it manages the buffer, treesitter
-- otherwise
local function math_zone(line_to_cursor)
  if vim.b.vimtex then
    if vim.fn["vimtex#syntax#in_mathzone"]() ~= 1 then
      return nil
    end
    local col = math.max(vim.fn.col(".") - 1, 1)
    for _, id in ipairs(vim.fn.synstack(vim.fn.line("."), col)) do
      local name = vim.fn.synIDattr(id, "name")
      if name == "texMathZoneTI" or name == "texMathZoneLI" then
        return "inline"
      end
    end
    return "display"
  end
  local parser_ok, parser = pcall(vim.treesitter.get_parser, 0)
  if not parser_ok or not parser then
    return nil
  end
  -- reparse (with injections) so the check sees the just-typed characters
  parser:parse(true)
  local ok, node = pcall(vim.treesitter.get_node, { ignore_injections = false })
  local saw_error = false
  while ok and node do
    local zone = MATH_NODES[node:type()]
    if zone then
      return zone
    end
    if node:type() == "latex_block" then
      -- markdown math without the latex parser installed; $$ means display
      local text_ok, text = pcall(vim.treesitter.get_node_text, node, 0)
      return (text_ok and text:sub(1, 2) == "$$") and "display" or "inline"
    end
    saw_error = saw_error or node:type() == "ERROR"
    node = node:parent()
  end
  if saw_error then
    return delimiter_zone(line_to_cursor)
  end
end

local function in_mathzone(line_to_cursor)
  if vim.b.vimtex then
    return vim.fn["vimtex#syntax#in_mathzone"]() == 1
  end
  return math_zone(line_to_cursor) ~= nil
end

local function in_inline_math(line_to_cursor)
  return math_zone(line_to_cursor) == "inline"
end

local function in_display_math(line_to_cursor)
  return math_zone(line_to_cursor) == "display"
end

local function in_text(line_to_cursor)
  return not in_mathzone(line_to_cursor)
end

local function cond_and(...)
  local conds = { ... }
  return function(...)
    for _, cond in ipairs(conds) do
      if not cond(...) then
        return false
      end
    end
    return true
  end
end

-- the stored selection is consumed by the next expansion, so this is only
-- true between the store_selection_keys press and the snippet that uses it
local function has_visual()
  return vim.b.LUASNIP_SELECT_RAW ~= nil
end

local function vis()
  return f(function(_, snip)
    return snip.env.LS_SELECT_RAW
  end)
end

local function cap(n)
  return f(function(_, snip)
    return snip.captures[n]
  end)
end
"""


def generate_neovim_snippets(source: SnippetSource) -> str:
    regular: list[tuple[int, str]] = []
    auto: list[tuple[int, str]] = []
    for snippet in resolved(source, "neovim"):
        options = snippet["options"]
        is_regex = bool(snippet.get("regex"))
        trigger = substitute_variables(snippet["trigger"], source.variables)
        replacement = substitute_variables(snippet["replacement"], source.variables)

        context = [f"trig = {lua_quote(trigger)}"]
        if snippet.get("description"):
            context.append(f"desc = {lua_quote(snippet['description'])}")
        if is_regex:
            # the same javascript regex dialect obsidian uses
            context.append('trigEngine = "ecma"')
        # regex triggers and autosnippets are noise in completion menus
        if is_regex or options.get("auto"):
            context.append("hidden = true")
        # luasnip's default wordTrig = true blocks mid-word matching
        if is_regex or matches_in_word(options):
            context.append("wordTrig = false")
        if "priority" in snippet:
            # luasnip priorities must be positive (default 1000); yaml priorities are
            # offsets around 0
            context.append(f"priority = {1000 + int(snippet['priority'])}")

        conditions = []
        inline, display = options.get("inline_math"), options.get("display_math")
        if options.get("math") or (inline and display):
            conditions.append("in_mathzone")
        elif inline:
            conditions.append("in_inline_math")
        elif display:
            conditions.append("in_display_math")
        elif options.get("text"):
            conditions.append("in_text")
        if "${VISUAL}" in replacement:
            conditions.append("has_visual")
        if options.get("beginning_of_line"):
            conditions.append("line_begin")
        if len(conditions) == 1:
            context.append(f"condition = {conditions[0]}")
        elif conditions:
            context.append(f"condition = cond_and({', '.join(conditions)})")

        line = f"  s({{ {', '.join(context)} }}, {lua_replacement_nodes(replacement)}),"
        (auto if options.get("auto") else regular).append((len(trigger), line))

    parts = [
        NEOVIM_PRELUDE,
        "local snippets = {",
        *emission_order(regular),
        "}",
        "",
        "local autosnippets = {",
        *emission_order(auto),
        "}",
    ]
    verbatim = source.verbatim_snippets.get("neovim")
    if verbatim:
        parts += ["", *(v.strip() for v in verbatim)]
    parts += ["", "return snippets, autosnippets"]
    return "\n".join(parts) + "\n"


# registry


@dataclass(frozen=True)
class Artifact:
    platform: str
    key: str  # name within the platform's config table
    filename: str  # canonical name for `build --out`
    generate: Callable[[SnippetSource], str]


ARTIFACTS: dict[str, Artifact] = {
    "obsidian_snippets": Artifact(
        "obsidian", "snippets", "obsidian_snippets.js", generate_obsidian_snippets
    ),
    "obsidian_variables": Artifact(
        "obsidian", "variables", "obsidian_variables.json", generate_obsidian_variables
    ),
    "vscode": Artifact("vscode", "snippets", "latex.hsnips", generate_vscode_snippets),
    "neovim": Artifact("neovim", "snippets", "tex.lua", generate_neovim_snippets),
}
PLATFORMS = tuple(dict.fromkeys(a.platform for a in ARTIFACTS.values()))


def load_source(path: Path) -> SnippetSource:
    if not path.exists():
        raise SnippetError([f"{path} not found"])
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.load(f, Loader=YAML_LOADER) or {}
    except yaml.YAMLError as e:
        raise SnippetError([f"{path}: invalid yaml: {e}"]) from e
    if not isinstance(data, dict):
        raise SnippetError([f"{path}: top level must be a mapping"])
    snippets = data.get("snippets") or []
    if not isinstance(snippets, list):
        raise SnippetError([f"{path}: 'snippets' must be a list"])

    source = SnippetSource(
        snippets=snippets,
        variables=data.get("variables") or {},
        verbatim_snippets=data.get("verbatim_snippets") or {},
        default_options=(data.get("defaults") or {}).get("options") or {},
    )
    errors, source.warnings = validate(snippets, source.variables, source.default_options)
    if errors:
        raise SnippetError(errors, source.warnings)
    return source


def compile_all(source: SnippetSource) -> dict[str, str]:
    return {name: artifact.generate(source) for name, artifact in ARTIFACTS.items()}
