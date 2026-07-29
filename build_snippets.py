"""
snippet compiler for obsidian latex suite and vscode hypersnips.

compiles snippets from a single yaml source of truth (snippets.yaml) into
platform-specific formats for obsidian and vscode/cursor. supports regex and
plaintext triggers, platform-specific overrides, shared variables, default
options, and automatic handling of platform differences (spaces in vscode
triggers, translating [[n]] capture groups to hsnips javascript blocks).

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
    'priority', 'options', 'platforms',
}
# keys a platform override is allowed to replace
ALLOWED_OVERRIDE_KEYS = {
    'trigger', 'regex', 'replacement', 'description', 'priority', 'options',
}
ALLOWED_OPTION_KEYS = {
    'math', 'inline_math', 'display_math', 'text', 'code', 'auto', 'visual',
    'in_word', 'word_boundary', 'beginning_of_line', 'multi_line',
}
PLATFORMS = ('obsidian', 'vscode')

# context options only the obsidian builder understands; a snippet relying on
# these for scoping will fire unscoped in vscode
OBSIDIAN_ONLY_CONTEXTS = {'inline_math', 'display_math', 'code', 'visual'}

CAPTURE_GROUP_RE = re.compile(r'\[\[(\d+)\]\]')
VARIABLE_RE = re.compile(r'\{\{(\w+)\}\}')
HSNIPS_MATCH_REF_RE = re.compile(r'\bm\[(\d+)\]')


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
        platform: 'obsidian' or 'vscode'
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
    # (platform, trigger, is_regex, context, priority) -> first index seen
    seen: Dict[Tuple, int] = {}

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

        target_platforms = snippet.get('target_platforms') or []
        for platform in target_platforms:
            if platform not in PLATFORMS:
                errors.append(f"{where}: unknown target platform '{platform}'")

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
                # a snippet scoped only by an obsidian-only context has no
                # context at all in vscode and would fire everywhere
                if (
                    any(options.get(k) for k in OBSIDIAN_ONLY_CONTEXTS)
                    and not options.get('math')
                    and not options.get('text')
                ):
                    warnings.append(
                        f"{where}: scoped only by obsidian-only options "
                        f"({', '.join(sorted(OBSIDIAN_ONLY_CONTEXTS))} are "
                        f"ignored in vscode); it will fire unscoped in vscode "
                        f"unless target_platforms excludes it"
                    )

            if platform == 'obsidian' and not is_regex and '\n' in merged_trigger:
                errors.append(f"{where}: obsidian triggers may not contain newlines")

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
        if options.get('visual'):
            opts_str += 'v'
        if options.get('word_boundary'):
            opts_str += 'w'

        # convert {{VAR}} syntax to obsidian's native ${VAR} variables, which
        # latex suite substitutes from the generated variables file
        trigger = re.sub(r'\{\{(\w+)\}\}', r'${\1}', final_snippet['trigger'])

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
    hsnips_content = (
        "global\n"
        "function math(context) {\n"
        "    return context.scopes.findLastIndex(s => s.startsWith(\"meta.math\")) > "
        "context.scopes.findLastIndex(s => s.startsWith(\"comment\") || s.startsWith(\"meta.text.normal.tex\"));\n"
        "}\n"
        "function notmath(context) {\n"
        "    return context.scopes.findLastIndex(s => s.startsWith(\"meta.math\")) <= "
        "context.scopes.findLastIndex(s => s.startsWith(\"comment\") || s.startsWith(\"meta.text.normal.tex\"));\n"
        "}\n"
        "endglobal\n\n"
    )

    for snippet in snippets:
        if not targets_platform(snippet, 'vscode'):
            continue

        final_snippet = merge_for_platform(snippet, 'vscode', default_options)
        options = final_snippet['options']

        trigger = substitute_variables(final_snippet['trigger'], variables)

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
        # default in_word to true for better ux (allows xsr → x^{2})
        if options.get('in_word', True):
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

        hsnips_content += snippet_str

    # Append verbatim snippets if any
    if verbatim_snippets.get('vscode'):
        for s in verbatim_snippets['vscode']:
            hsnips_content += f"{s.strip()}\n\n"

    return hsnips_content


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

    # Support both old single path and new comma-separated list for VSCode
    latex_paths_env = os.getenv('LATEX_SNIPPETS_PATHS') or os.getenv('LATEX_SNIPPETS_PATH')
    latex_paths = parse_paths_list(latex_paths_env)

    # Fallback to default if no paths specified
    if not latex_paths:
        latex_paths = [resolve_path('latex.hsnips')]

    # Canonical, git-diffable copies of every generated file
    build_copies = {
        'obsidian_snippets': BUILD_DIR / 'obsidian_snippets.js',
        'obsidian_variables': BUILD_DIR / 'obsidian_variables.json',
        'latex': BUILD_DIR / 'latex.hsnips',
    }

    # Collect all output paths for cleaning
    output_paths = (
        [obsidian_path, obsidian_vars_path] + latex_paths
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

    write_output(obsidian_path, obsidian_content)
    write_output(build_copies['obsidian_snippets'], obsidian_content)
    write_output(obsidian_vars_path, obsidian_vars_content)
    write_output(build_copies['obsidian_variables'], obsidian_vars_content)
    for latex_path in latex_paths:
        write_output(latex_path, latex_content)
    write_output(build_copies['latex'], latex_content)

    print("\n✓ Snippet build process completed successfully!")


if __name__ == "__main__":
    main()
