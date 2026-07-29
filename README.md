# snipsmith

a simple and powerful way to manage your latex snippets for obsidian and vscode from a single source of truth

![the same schrödinger equation typed in obsidian and vscode, from one snippets.yaml](assets/demo.gif)

## what is this?

this project provides a unified system for managing your latex snippets. all your snippets live in a single, easy-to-read `snippets.yaml` file. a python script then compiles this file into the platform-specific formats required by obsidian-latex-suite and vscode's hypersnips extension. it's built to be flexible, allowing for platform-specific overrides, shared variables, and more.

## prerequisites

this project is built around two key tools; you'll need them installed and configured to get started. if you aren't already using them both there's probably no reason to be reading this right now.

-   [obsidian-latex-suite](https://github.com/artisticat1/obsidian-latex-suite)
-   [hypersnips for vscode](https://marketplace.visualstudio.com/items?itemName=draivin.hsnips)

## setup

in obsidian-latex-suite's settings:
-   enable `Load snippets from file or folder`
-   enable `Load snippet variables from file or folder`
-   take note of the file paths you choose for both of these settings

in vscode/cursor:
-   create an empty `latex.hsnips` file in your hypersnips snippets directory
-   take note of its file path

now, in the `.env` file in the root of this project, fill in the absolute paths you noted in the previous step. here's what your `.env` file should look like:

```
# .env file
OBSIDIAN_SNIPPETS_PATH="/path/to/your/obsidian/snippets.js"
OBSIDIAN_VARIABLES_PATH="/path/to/your/obsidian/variables.json"

# single path (for vscode only):
LATEX_SNIPPETS_PATH="/path/to/your/latex/snippets.hsnips"

# or multiple paths (for both vscode and cursor, for example):
LATEX_SNIPPETS_PATHS="/path/to/vscode/latex.hsnips,/path/to/cursor/latex.hsnips"
```

**note:** the script supports both `LATEX_SNIPPETS_PATH` (single) and `LATEX_SNIPPETS_PATHS` (multiple, comma-separated). use whichever fits your setup.

## building snippets

assuming you have python 3 installed (you probably do), open your terminal and run

```
make snippets
```

this command will create a local python virtual environment, install the necessary dependencies, and generate your snippet files in the locations you specified. a canonical copy of every generated file is also written to the `build/` directory in this repo, so you can `git diff build/` to review exactly what changed before it goes live.

the build validates `snippets.yaml` first (unknown keys, invalid regexes, capture group references that don't exist, duplicate snippets, and more) and refuses to write anything if there are errors. to validate without writing any files, run

```
make check
```

ci runs the same validation on every push and also verifies that the committed files in `build/` match a fresh build, so behavior changes can't sneak in without showing up in a reviewable diff.

## cleaning up

to remove all generated files (at the paths specified in your `.env`) and the python virtual environment, simply run

```
make clean
```

that's pretty much it, enjoy! in the existing `snippets.yaml` i've provided the snippets i actually use in my setup, if that's useful. they're a combination of the default obsidian-latex-suite snippets, snippets from [here](https://github.com/Einlar/latex_snippets/blob/master/hsnips/latex.hsnips), and my own personal snippets.

## features

### regex vs plaintext triggers

by default, all snippet triggers are treated as plaintext. if you want a snippet to use a regex pattern trigger instead, add `regex: true` to the snippet definition:

```yaml
snippets:
  # this trigger is plaintext (default)
  - trigger: 'hello'
    replacement: "Hello, World!"
    options:
      text: true

  # this trigger is regex
  - trigger: '([a-zA-Z])bar'
    replacement: "\\bar{[[0]]}"
    regex: true
    options:
      math: true
```

**implementation details:**
- for obsidian: regex snippets include the `r` flag in options, plaintext snippets don't
- for vscode: regex triggers are wrapped in backticks (`` `trigger` ``), plaintext triggers are not
- plaintext triggers with spaces are automatically converted to escaped regex for vscode (since hypersnips doesn't support spaces in plaintext triggers)
- forward slashes in regex triggers are automatically escaped for obsidian (the snippet file is parsed as javascript, where triggers are `/.../` regex literals)

### capture groups in regex replacements

reference regex capture groups in replacements with obsidian-latex-suite's `[[n]]` syntax (`[[0]]` is the first capture group). the build script automatically translates `[[n]]` into hypersnips' inline javascript form (`` ``rv = m[n+1]`` ``) for vscode, so one replacement works on both platforms:

```yaml
snippets:
  - trigger: ([a-zA-Z])und
    replacement: "\\underline{[[0]]}"
    regex: true
    options:
      math: true
```

you only need a `platforms.vscode.replacement` override when the vscode version genuinely differs from a mechanical translation.

### default options

set options once for all snippets in the top-level `defaults` section; individual snippets (and platform overrides) merge on top and can override any default:

```yaml
defaults:
  options:
    auto: true

snippets:
  # inherits auto: true
  - trigger: mk
    replacement: $$1$
    options:
      text: true

  # opts out (only triggers on tab)
  - trigger: ([a-zA-Z])dot
    replacement: "\\dot{[[0]]}"
    regex: true
    options:
      math: true
      auto: false
```

### in-word triggering

by default, snippets trigger inside words (`in_word: true`). this means `xsr` will expand to `x^{2}`, not just `x sr`. if you want a snippet to require word boundaries (only trigger after a space), set `in_word: false` in the snippet options.

### multiple output paths

you can export snippets to multiple vscode/cursor instances by specifying multiple paths in `LATEX_SNIPPETS_PATHS` (comma-separated). this is useful if you use both vscode and cursor, or have multiple vscode profiles.

### platform-specific overrides

each snippet can have platform-specific overrides for obsidian and vscode. this lets you customize triggers, replacements, or options per platform while keeping most of the snippet definition shared.

### shared variables

define variables once in the `variables` section and reference them in triggers and replacements using `{{VARIABLE_NAME}}`. the build script substitutes these automatically.
