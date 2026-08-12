"""
snippet compiler for obsidian latex suite, vscode hypersnips, and neovim luasnip.

compiles snippets from a single yaml source of truth (snippets.yaml) into
platform-specific formats for obsidian, vscode/cursor, and neovim. supports
regex and plaintext triggers, platform-specific overrides, shared variables,
default options, and automatic handling of platform differences (spaces in
vscode triggers, translating [[n]] capture groups to hsnips javascript blocks
or luasnip function nodes).

the yaml is validated before anything is written, so malformed snippets fail
the build instead of silently producing broken output.
"""

from typing import Any, Dict, List, Optional, Tuple
import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

SNIPPETS_FILE = Path("snippets.yaml")
BUILD_DIR = Path("build")

ALLOWED_SNIPPET_KEYS = {
    'trigger', 'regex', 'replacement', 'description', 'target_platforms',
    'priority', 'options', 'platforms', 'excluded_macros',
}
# keys a platform override is allowed to replace
ALLOWED_OVERRIDE_KEYS = {
    'trigger', 'regex', 'replacement', 'description', 'priority', 'options',
    'excluded_macros',
}
ALLOWED_OPTION_KEYS = {
    'math', 'inline_math', 'display_math', 'text', 'code', 'auto',
    'in_word', 'word_boundary', 'beginning_of_line', 'multi_line',
}
PLATFORMS = ('obsidian', 'vscode', 'neovim')

IGNORED_CONTEXTS = {
    'vscode': {'inline_math', 'display_math', 'code'},
    'neovim': {'code'},
}

CAPTURE_GROUP_RE = re.compile(r'\[\[(\d+)\]\]')
VARIABLE_RE = re.compile(r'\{\{(\w+)\}\}')
HSNIPS_MATCH_REF_RE = re.compile(r'\bm\[(\d+)\]')
NEOVIM_TOKEN_RE = re.compile(
    CAPTURE_GROUP_RE.pattern + r'|\$\{(\d+):([^}]*)\}|\$(\d+)|\$\{VISUAL\}'
)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def merge_for_platform(
    snippet: Dict[str, Any],
    platform: str,
    default_options: Dict[str, Any]
) -> Dict[str, Any]:
    """
    merge a snippet with the global default options and its platform override.

    precedence (lowest to highest): defaults < snippet < platform override.

    args:
        snippet: the raw snippet definition
        platform: one of PLATFORMS
        default_options: global default options from the yaml 'defaults' key

    returns:
        the merged snippet with a fully-resolved 'options' dict
    """
    override = (snippet.get('platforms') or {}).get(platform) or {}
    merged = {**snippet, **override}
    merged['options'] = {
        **default_options,
        **(snippet.get('options') or {}),
        **(override.get('options') or {}),
    }
    return merged


def targets_platform(snippet: Dict[str, Any], platform: str) -> bool:
    """check whether a snippet should be generated for the given platform."""
    target_platforms = snippet.get('target_platforms')
    return not target_platforms or platform in target_platforms


def substitute_variables(text: str, variables: Dict[str, str]) -> str:
    """replace {{VAR}} references with their values from the variables map."""
    for var, val in variables.items():
        text = text.replace(f"{{{{{var}}}}}", val)
    return text


def escape_regex_slashes(pattern: str) -> str:
    """
    escape unescaped forward slashes in a regex pattern so it can be safely
    embedded in a javascript /.../ regex literal (obsidian snippet files are
    parsed as javascript).
    """
    out: List[str] = []
    escaped = False
    for ch in pattern:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == '\\':
            out.append(ch)
            escaped = True
        elif ch == '/':
            out.append('\\/')
        else:
            out.append(ch)
    return ''.join(out)


def translate_capture_groups(replacement: str) -> str:
    """
    translate obsidian-style [[n]] capture group references into hsnips
    inline javascript blocks. obsidian's [[0]] is the first capture group,
    which hsnips exposes as m[1].
    """
    return CAPTURE_GROUP_RE.sub(
        lambda m: f"``rv = m[{int(m.group(1)) + 1}]``",
        replacement,
    )


def lua_quote(text: str) -> str:
    """escape a string for embedding in a double-quoted lua string literal."""
    escaped = (
        text.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\t', '\\t')
        .replace('\r', '\\r')
        .replace('\n', '\\n')
    )
    return f'"{escaped}"'


def iter_neovim_tokens(replacement: str):
    """yield (start, end, kind, n, default) for each [[n]], $n/${n:default}, or ${VISUAL} token."""
    for m in NEOVIM_TOKEN_RE.finditer(replacement):
        if m.group(1) is not None:
            yield m.start(), m.end(), 'capture', int(m.group(1)), None
        elif m.group(0) == '${VISUAL}':
            yield m.start(), m.end(), 'visual', None, None
        else:
            yield (m.start(), m.end(), 'tabstop',
                   int(m.group(2) or m.group(4)), m.group(3))


def _append_lua_text(nodes: List[str], text: str) -> None:
    """append a luasnip text node for a (possibly multiline) literal."""
    if not text:
        return
    lines = text.split('\n')
    if len(lines) == 1:
        nodes.append(f"t({lua_quote(text)})")
    else:
        nodes.append("t({ " + ", ".join(lua_quote(line) for line in lines) + " })")


def lua_replacement_nodes(replacement: str) -> str:
    """
    translate a replacement's literals, $n tabstops, and [[n]] captures into
    a luasnip node list.
    """
    nodes: List[str] = []
    seen_stops = set()
    pos = 0
    for start, end, kind, n, default in iter_neovim_tokens(replacement):
        _append_lua_text(nodes, replacement[pos:start])
        pos = end
        if kind == 'capture':
            nodes.append(f"cap({n + 1})")
        elif kind == 'visual':
            nodes.append("vis()")
        elif n in seen_stops:
            nodes.append(f"rep({n})")
        else:
            seen_stops.add(n)
            nodes.append(
                f"i({n})" if default is None
                else f"i({n}, {lua_quote(default)})"
            )
    _append_lua_text(nodes, replacement[pos:])
    if not nodes:
        nodes.append('t("")')
    return "{ " + ", ".join(nodes) + " }"


def matches_in_word(options: Dict[str, Any]) -> bool:
    """whether the snippet may trigger in the middle of a word."""
    return options.get('in_word', True) and not options.get('word_boundary')


def emission_order(entries: List[Tuple[int, str]]) -> List[str]:
    """
    order (trigger length, text) entries longest-trigger-first; hsnips and
    luasnip both fall back to definition order on priority ties, so this
    reproduces obsidian's tie-breaking on every platform.
    """
    return [text for _, text in sorted(entries, key=lambda e: -e[0])]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate(
    snippets: List[Dict[str, Any]],
    variables: Dict[str, str],
    default_options: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """
    validate the snippet definitions before any output is generated.

    args:
        snippets: list of snippet definitions
        variables: dictionary of variable names to values
        default_options: global default options

    returns:
        (errors, warnings) as lists of human-readable messages. errors should
        abort the build; warnings are informational.
    """
    errors: List[str] = []
    warnings: List[str] = []
    # (platform, trigger, is_regex, context, priority, replacement) -> first
    # index seen
    seen: Dict[Tuple, int] = {}
    # snippet content ignoring target_platforms -> first index seen
    seen_content: Dict[str, int] = {}

    def check_variable_refs(text: str, where: str) -> None:
        for var in VARIABLE_RE.findall(text):
            if var not in variables:
                errors.append(f"{where}: unknown variable {{{{{var}}}}}")

    def check_option_keys(options: Optional[Dict[str, Any]], where: str) -> None:
        for key in (options or {}):
            if key not in ALLOWED_OPTION_KEYS:
                errors.append(f"{where}: unknown option '{key}'")

    for i, snippet in enumerate(snippets):
        trigger = snippet.get('trigger')
        where = f"snippet #{i + 1} ({trigger!r})"

        if not isinstance(trigger, str) or not trigger:
            errors.append(f"{where}: 'trigger' must be a non-empty string")
            continue
        if not isinstance(snippet.get('replacement'), str):
            errors.append(f"{where}: 'replacement' must be a string")
            continue

        for key in snippet:
            if key not in ALLOWED_SNIPPET_KEYS:
                errors.append(f"{where}: unknown key '{key}'")
        check_option_keys(snippet.get('options'), where)

        content_key = json.dumps(
            {k: v for k, v in snippet.items() if k != 'target_platforms'},
            sort_keys=True, default=str,
        )
        if content_key in seen_content:
            warnings.append(
                f"{where}: identical to snippet #{seen_content[content_key]} "
                f"except for target_platforms; merge them into one entry"
            )
        else:
            seen_content[content_key] = i + 1

        target_platforms = snippet.get('target_platforms') or []
        for platform in target_platforms:
            if platform not in PLATFORMS:
                errors.append(f"{where}: unknown target platform '{platform}'")

        excluded = snippet.get('excluded_macros')
        if excluded is not None and (
            not isinstance(excluded, list)
            or not all(isinstance(m, str) for m in excluded)
        ):
            errors.append(f"{where}: excluded_macros must be a list of strings")

        for platform, override in (snippet.get('platforms') or {}).items():
            if platform not in PLATFORMS:
                errors.append(f"{where}: unknown override platform '{platform}'")
                continue
            for key in (override or {}):
                if key not in ALLOWED_OVERRIDE_KEYS:
                    errors.append(
                        f"{where}: platform override may not set '{key}'"
                    )
            check_option_keys((override or {}).get('options'), where)

        check_variable_refs(trigger, where)
        check_variable_refs(snippet['replacement'], where)

        for platform in PLATFORMS:
            if not targets_platform(snippet, platform):
                continue
            merged = merge_for_platform(snippet, platform, default_options)
            options = merged['options']
            merged_trigger = substitute_variables(merged['trigger'], variables)
            is_regex = bool(merged.get('regex', False))

            group_count = 0
            if is_regex:
                try:
                    group_count = re.compile(merged_trigger).groups
                except re.error as e:
                    errors.append(f"{where}: invalid regex trigger: {e}")
                    continue
            else:
                if CAPTURE_GROUP_RE.search(merged['replacement']):
                    warnings.append(
                        f"{where}: replacement uses [[n]] but the trigger is "
                        f"not a regex"
                    )

            # capture group references must exist in the trigger
            if is_regex:
                for n in CAPTURE_GROUP_RE.findall(merged['replacement']):
                    if int(n) + 1 > group_count:
                        errors.append(
                            f"{where}: replacement references [[{n}]] but the "
                            f"trigger only has {group_count} capture group(s)"
                        )
                if platform == 'vscode':
                    for n in HSNIPS_MATCH_REF_RE.findall(merged['replacement']):
                        if int(n) > group_count:
                            errors.append(
                                f"{where}: replacement references m[{n}] but "
                                f"the trigger only has {group_count} capture "
                                f"group(s)"
                            )

            # hsnips has no escaping mechanism for these characters
            if platform == 'vscode':
                if is_regex and '`' in merged_trigger:
                    errors.append(
                        f"{where}: backtick in regex trigger would corrupt the "
                        f"hsnips file"
                    )
                if '"' in merged.get('description', ''):
                    errors.append(
                        f"{where}: double quote in description would corrupt "
                        f"the hsnips file"
                    )
                if '${VISUAL}' in merged['replacement']:
                    errors.append(
                        f"{where}: ${{VISUAL}} is not supported for vscode; "
                        f"exclude vscode via target_platforms"
                    )
            ignored = IGNORED_CONTEXTS.get(platform)
            if (
                ignored
                and any(options.get(k) for k in ignored)
                and not any(
                    options.get(k)
                    for k in {'math', 'inline_math', 'display_math', 'text',
                              'code'} - ignored
                )
            ):
                warnings.append(
                    f"{where}: scoped only by options the {platform} builder "
                    f"ignores ({', '.join(sorted(ignored))}); it will fire "
                    f"unscoped in {platform} unless target_platforms "
                    f"excludes it"
                )

            if platform == 'neovim':
                replacement = merged['replacement']
                if '``' in replacement:
                    errors.append(
                        f"{where}: hsnips javascript (``...``) in replacement "
                        f"is not supported for neovim; add a platforms.neovim "
                        f"override or exclude neovim via target_platforms"
                    )
                stops = [
                    (start, n, default)
                    for start, _, kind, n, default
                    in iter_neovim_tokens(replacement)
                    if kind == 'tabstop'
                ]
                zero_positions = [p for p, n, _ in stops if n == 0]
                if len(zero_positions) > 1:
                    errors.append(
                        f"{where}: $0 appears more than once; luasnip cannot "
                        f"mirror the final tabstop"
                    )
                if any(n == 0 and d is not None for _, n, d in stops):
                    errors.append(
                        f"{where}: ${{0:default}} is not supported for neovim "
                        f"($0 is the final cursor position)"
                    )
                if zero_positions and any(
                    n != 0 and p > zero_positions[0] for p, n, _ in stops
                ):
                    warnings.append(
                        f"{where}: $0 appears before other tabstops; luasnip "
                        f"visits $0 last (unlike obsidian, which visits it "
                        f"first), so add a platforms.neovim override if the "
                        f"jump order matters"
                    )
                for _, n, default in stops:
                    if default and ('$' in default or '{' in default
                                    or CAPTURE_GROUP_RE.search(default)):
                        errors.append(
                            f"{where}: tabstop default {default!r} is too "
                            f"complex for the neovim generator (no nested "
                            f"tabstops, captures, or braces)"
                        )

            if (
                platform in ('obsidian', 'neovim')
                and not is_regex and '\n' in merged_trigger
            ):
                errors.append(
                    f"{where}: {platform} triggers may not contain newlines"
                )

            context = tuple(
                bool(options.get(k))
                for k in ('math', 'inline_math', 'display_math', 'text', 'code')
            )
            key = (
                platform, merged_trigger, is_regex, context,
                merged.get('priority', 0), merged['replacement'],
            )
            if key in seen:
                warnings.append(
                    f"{where}: duplicate of snippet #{seen[key]} for "
                    f"{platform} (same trigger, context, priority, and "
                    f"replacement)"
                )
            else:
                seen[key] = i + 1

    return errors, warnings


# ---------------------------------------------------------------------------
# generators (pure: yaml data in, file content out)
# ---------------------------------------------------------------------------

def generate_obsidian_snippets(
    snippets: List[Dict[str, Any]],
    verbatim_snippets: Dict[str, List[str]],
    default_options: Dict[str, Any]
) -> str:
    """
    generate the contents of the obsidian snippets .js file.

    args:
        snippets: list of snippet definitions
        verbatim_snippets: platform-specific verbatim snippets to append
        default_options: global default options

    returns:
        the file content as a string
    """
    output_lines = []

    for snippet in snippets:
        if not targets_platform(snippet, 'obsidian'):
            continue

        final_snippet = merge_for_platform(snippet, 'obsidian', default_options)
        options = final_snippet['options']

        # build options string for obsidian
        opts_str = ""
        # regex flag - snippets are plaintext by default unless explicitly set to true
        if final_snippet.get('regex', False):
            opts_str += 'r'
        if options.get('math'):
            opts_str += 'm'
        if options.get('inline_math'):
            opts_str += 'n'
        if options.get('display_math'):
            opts_str += 'M'
        if options.get('text'):
            opts_str += 't'
        if options.get('code'):
            opts_str += 'c'
        if options.get('auto'):
            opts_str += 'A'
        if not matches_in_word(options):
            opts_str += 'w'

        # convert {{VAR}} syntax to obsidian's native ${VAR} variables, which
        # latex suite substitutes from the generated variables file
        trigger = VARIABLE_RE.sub(r'${\1}', final_snippet['trigger'])

        # for regex triggers, wrap in slashes; for plaintext, use string format
        if final_snippet.get('regex', False):
            trigger_str = f"trigger: /{escape_regex_slashes(trigger)}/"
        else:
            trigger_str = f"trigger: {json.dumps(trigger)}"

        line_parts = [
            trigger_str,
            f"replacement: {json.dumps(final_snippet['replacement'])}",
            f"options: {json.dumps(opts_str)}",
            f"description: {json.dumps(final_snippet.get('description', ''))}"
        ]

        if 'priority' in final_snippet:
            line_parts.append(f"priority: {final_snippet['priority']}")
        if final_snippet.get('excluded_macros'):
            line_parts.append(
                f"excludedMacros: {json.dumps(final_snippet['excluded_macros'])}"
            )

        output_lines.append(f"    {{ {', '.join(line_parts)} }}")

    file_content = "[\n" + ",\n".join(output_lines)

    # Append verbatim snippets if any
    if verbatim_snippets.get('obsidian'):
        file_content += ",\n"
        verbatim_lines = [f"    {s.strip()}" for s in verbatim_snippets['obsidian']]
        file_content += ",\n".join(verbatim_lines)

    file_content += "\n]\n"
    return file_content


def generate_obsidian_variables(variables: Dict[str, str]) -> str:
    """
    generate the contents of the obsidian variables .json file.

    args:
        variables: dictionary of variable names to values

    returns:
        the file content as a string
    """
    obsidian_vars = {f"${{{key}}}": value for key, value in variables.items()}
    return json.dumps(obsidian_vars, indent=4)


def generate_latex_snippets(
    snippets: List[Dict[str, Any]],
    variables: Dict[str, str],
    verbatim_snippets: Dict[str, List[str]],
    default_options: Dict[str, Any]
) -> str:
    """
    generate the contents of the latex.hsnips file.

    args:
        snippets: list of snippet definitions
        variables: dictionary of variable names to values
        verbatim_snippets: platform-specific verbatim snippets to append
        default_options: global default options

    returns:
        the file content as a string
    """
    # math() also matches vscode's markdown math scopes; notmath() stays
    # quiet inside code (markdown fenced/inline code, latex verbatim)
    hsnips_content = (
        "global\n"
        "function math(context) {\n"
        "    return context.scopes.findLastIndex(s => s.startsWith(\"meta.math\") || s.startsWith(\"meta.embedded.math\")) > "
        "context.scopes.findLastIndex(s => s.startsWith(\"comment\") || s.startsWith(\"meta.text.normal.tex\"));\n"
        "}\n"
        "function notmath(context) {\n"
        "    return !math(context) && !context.scopes.some(s => "
        "s.startsWith(\"markup.fenced_code\") || s.startsWith(\"markup.raw\") || s.startsWith(\"markup.inline.raw\"));\n"
        "}\n"
        "endglobal\n\n"
    )

    entries: List[Tuple[int, str]] = []

    for snippet in snippets:
        if not targets_platform(snippet, 'vscode'):
            continue

        final_snippet = merge_for_platform(snippet, 'vscode', default_options)
        options = final_snippet['options']

        trigger = substitute_variables(final_snippet['trigger'], variables)
        trigger_length = len(trigger)

        replacement = substitute_variables(final_snippet['replacement'], variables)
        # translate obsidian-style [[n]] capture groups; a no-op for snippets
        # that provide an explicit vscode replacement in hsnips syntax
        replacement = translate_capture_groups(replacement)
        # for hsnips, backslash is a special character in the body
        replacement = replacement.replace('\\', '\\\\')

        description = final_snippet.get('description', '')

        # build flags for vscode
        flags = ""
        if options.get('auto'):
            flags += 'A'
        if matches_in_word(options):
            flags += 'i'
        if options.get('word_boundary'):
            flags += 'w'
        if options.get('beginning_of_line'):
            flags += 'b'
        if options.get('multi_line'):
            flags += 'M'

        # Build context
        context = ""
        if options.get('math'):
            context = "context math(context)\n"
        elif options.get('text'):
            context = "context notmath(context)\n"

        # Build snippet string
        snippet_str = ""
        if 'priority' in final_snippet:
            snippet_str += f"priority {final_snippet['priority']}\n"

        if context:
            snippet_str += context

        # determine if trigger is regex or plaintext
        # by default, snippets are plaintext (safer default)
        is_regex = final_snippet.get('regex', False)

        # vscode/hsnips doesn't support spaces in plaintext triggers
        # so we convert plaintext triggers with spaces to escaped regex
        if not is_regex and ' ' in trigger:
            trigger = re.escape(trigger)
            is_regex = True  # treat as regex for vscode only

        if is_regex:
            # regex triggers use backticks
            snippet_str += f'snippet `{trigger}` "{description}" {flags}\n'
        else:
            # plaintext triggers are unquoted
            snippet_str += f'snippet {trigger} "{description}" {flags}\n'

        snippet_str += f'{replacement}\n'
        snippet_str += 'endsnippet\n\n'

        entries.append((trigger_length, snippet_str))

    hsnips_content += "".join(emission_order(entries))

    # Append verbatim snippets if any
    if verbatim_snippets.get('vscode'):
        for s in verbatim_snippets['vscode']:
            hsnips_content += f"{s.strip()}\n\n"

    return hsnips_content


NEOVIM_PRELUDE = '''\
-- generated by snipsmith (build_snippets.py); do not edit by hand.

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
'''


def generate_neovim_snippets(
    snippets: List[Dict[str, Any]],
    variables: Dict[str, str],
    verbatim_snippets: Dict[str, List[str]],
    default_options: Dict[str, Any]
) -> str:
    """
    generate the contents of the luasnip snippet file.

    args:
        snippets: list of snippet definitions
        variables: dictionary of variable names to values
        verbatim_snippets: platform-specific verbatim snippets to append
        default_options: global default options

    returns:
        the file content as a string
    """
    regular: List[Tuple[int, str]] = []
    auto: List[Tuple[int, str]] = []

    for snippet in snippets:
        if not targets_platform(snippet, 'neovim'):
            continue

        final_snippet = merge_for_platform(snippet, 'neovim', default_options)
        options = final_snippet['options']
        is_regex = bool(final_snippet.get('regex'))

        trigger = substitute_variables(final_snippet['trigger'], variables)
        replacement = substitute_variables(final_snippet['replacement'], variables)

        context = [f"trig = {lua_quote(trigger)}"]
        description = final_snippet.get('description', '')
        if description:
            context.append(f"desc = {lua_quote(description)}")
        if is_regex:
            # the same javascript regex dialect obsidian uses
            context.append('trigEngine = "ecma"')
        # regex triggers and autosnippets are noise in completion menus
        if is_regex or options.get('auto'):
            context.append("hidden = true")

        # luasnip's default wordTrig = true blocks mid-word matching
        if is_regex or matches_in_word(options):
            context.append("wordTrig = false")

        if 'priority' in final_snippet:
            # luasnip priorities must be positive (default 1000); yaml
            # priorities are offsets around 0
            context.append(f"priority = {1000 + int(final_snippet['priority'])}")

        conditions = []
        inline = options.get('inline_math')
        display = options.get('display_math')
        if options.get('math') or (inline and display):
            conditions.append('in_mathzone')
        elif inline:
            conditions.append('in_inline_math')
        elif display:
            conditions.append('in_display_math')
        elif options.get('text'):
            conditions.append('in_text')
        if '${VISUAL}' in replacement:
            conditions.append('has_visual')
        if options.get('beginning_of_line'):
            conditions.append('line_begin')
        if len(conditions) == 1:
            context.append(f"condition = {conditions[0]}")
        elif conditions:
            context.append(f"condition = cond_and({', '.join(conditions)})")

        line = (
            f"  s({{ {', '.join(context)} }}, "
            f"{lua_replacement_nodes(replacement)}),"
        )
        (auto if options.get('auto') else regular).append((len(trigger), line))

    parts = [
        NEOVIM_PRELUDE,
        "local snippets = {", *emission_order(regular), "}", "",
        "local autosnippets = {", *emission_order(auto), "}",
    ]

    if verbatim_snippets.get('neovim'):
        parts.append("")
        for v in verbatim_snippets['neovim']:
            parts.append(v.strip())

    parts.append("")
    parts.append("return snippets, autosnippets")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# file handling
# ---------------------------------------------------------------------------

def write_output(path: Path, content: str) -> None:
    """
    atomically write content to a file: write to a temp file in the same
    directory, then rename over the destination. a failed build can never
    leave a truncated snippet file behind.

    args:
        path: destination file path
        content: file content to write
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + '.tmp')
    tmp_path.write_text(content, encoding='utf-8')
    tmp_path.replace(path)
    print(f"✓ Built {path}")


def resolve_path(path: str) -> Path:
    """
    resolve a path, expanding ~ and making it absolute.

    args:
        path: path string to resolve

    returns:
        resolved path object
    """
    return Path(path).expanduser().resolve()


def parse_paths_list(env_value: Optional[str]) -> List[Path]:
    """
    parse comma-separated paths from environment variable.

    args:
        env_value: comma-separated string of paths

    returns:
        list of resolved path objects
    """
    if not env_value:
        return []
    return [resolve_path(p.strip()) for p in env_value.split(',') if p.strip()]


def paths_from_env(name: str, default: str) -> List[Path]:
    """resolve output paths from $<name>S (comma-separated) or $<name>."""
    paths = parse_paths_list(os.getenv(name + 'S') or os.getenv(name))
    return paths or [resolve_path(default)]


def clean_files(paths: List[Path]) -> None:
    """
    delete the files at the given paths.

    args:
        paths: list of file paths to remove
    """
    print(">>> Cleaning up generated files...")
    for path in paths:
        try:
            path.unlink()
            print(f"    ✓ Removed {path}")
        except FileNotFoundError:
            print(f"    - Not found, skipping: {path}")
        except Exception as e:
            print(f"    ✗ Error removing {path}: {e}")


def main() -> None:
    """main entry point for the snippet builder."""
    parser = argparse.ArgumentParser(description="Build or clean snippet files.")
    parser.add_argument('--clean', action='store_true', help='Remove generated snippet files.')
    parser.add_argument('--check', action='store_true', help='Validate snippets.yaml without writing any files.')
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Parse output paths
    obsidian_path = resolve_path(
        os.getenv('OBSIDIAN_SNIPPETS_PATH', 'obsidian_snippets.js')
    )
    obsidian_vars_path = resolve_path(
        os.getenv('OBSIDIAN_VARIABLES_PATH', 'obsidian_variables.json')
    )

    latex_paths = paths_from_env('LATEX_SNIPPETS_PATH', 'latex.hsnips')
    neovim_paths = paths_from_env('NEOVIM_SNIPPETS_PATH', 'tex.lua')

    # Canonical, git-diffable copies of every generated file
    build_copies = {
        'obsidian_snippets': BUILD_DIR / 'obsidian_snippets.js',
        'obsidian_variables': BUILD_DIR / 'obsidian_variables.json',
        'latex': BUILD_DIR / 'latex.hsnips',
        'neovim': BUILD_DIR / 'tex.lua',
    }

    # Collect all output paths for cleaning
    output_paths = (
        [obsidian_path, obsidian_vars_path] + latex_paths + neovim_paths
        + list(build_copies.values())
    )

    if args.clean:
        clean_files(output_paths)
        return

    # Load snippets configuration
    if not SNIPPETS_FILE.exists():
        print(f"✗ Error: {SNIPPETS_FILE} not found")
        sys.exit(1)

    with SNIPPETS_FILE.open("r", encoding='utf-8') as f:
        data = yaml.safe_load(f)

    snippets = data.get('snippets', [])
    variables = data.get('variables', {})
    verbatim_snippets = data.get('verbatim_snippets', {})
    default_options = (data.get('defaults') or {}).get('options') or {}

    # Validate before writing anything
    errors, validation_warnings = validate(snippets, variables, default_options)
    for warning in validation_warnings:
        print(f"⚠ Warning: {warning}")
    if errors:
        for error in errors:
            print(f"✗ Error: {error}")
        print(f"\n✗ Validation failed with {len(errors)} error(s); nothing was written.")
        sys.exit(1)

    if args.check:
        print(f"✓ Validated {len(snippets)} snippets successfully!")
        return

    # Generate each output once, then write it to every destination
    obsidian_content = generate_obsidian_snippets(
        snippets, verbatim_snippets, default_options
    )
    obsidian_vars_content = generate_obsidian_variables(variables)
    latex_content = generate_latex_snippets(
        snippets, variables, verbatim_snippets, default_options
    )
    neovim_content = generate_neovim_snippets(
        snippets, variables, verbatim_snippets, default_options
    )

    write_output(obsidian_path, obsidian_content)
    write_output(build_copies['obsidian_snippets'], obsidian_content)
    write_output(obsidian_vars_path, obsidian_vars_content)
    write_output(build_copies['obsidian_variables'], obsidian_vars_content)
    for latex_path in latex_paths:
        write_output(latex_path, latex_content)
    write_output(build_copies['latex'], latex_content)
    for neovim_path in neovim_paths:
        write_output(neovim_path, neovim_content)
    write_output(build_copies['neovim'], neovim_content)

    print("\n✓ Snippet build process completed successfully!")


if __name__ == "__main__":
    main()
