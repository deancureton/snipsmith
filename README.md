# snipsmith

a simple and powerful way to manage your latex snippets for obsidian, vscode, and neovim from a single source of truth

![the same schrödinger equation typed in obsidian, vscode, and neovim, from one snippets.yaml](assets/demo.gif)

## what is this?

this project provides a unified system for managing your latex snippets. all your snippets live in a single, easy-to-read `snippets.yaml` file. the `snipsmith` cli then compiles this file into the platform-specific formats required by obsidian-latex-suite, vscode's hypersnips extension, and neovim's luasnip. it's built to be flexible, allowing for platform-specific overrides, shared variables, and more.

## install

```
brew install deancureton/tap/snipsmith
```

or, with python 3.11+:

```
uv tool install git+https://github.com/deancureton/snipsmith
```

## setup

```
snipsmith init
```

this detects your obsidian vaults, vscode-family editors (vscode, cursor, vscodium, windsurf, antigravity), and neovim, then walks you through each one it finds. every prompt defaults to what it detected, so you can mostly press enter.

for each editor it offers to install the plugin snipsmith writes snippets for, and points it at the generated files:

-   **obsidian**: [latex suite](https://github.com/artisticat1/obsidian-latex-suite)
-   **vscode / cursor**: [hypersnips](https://marketplace.visualstudio.com/items?itemName=draivin.hsnips)
-   **neovim**: [luasnip](https://github.com/L3MON4D3/LuaSnip), plus its `jsregexp` extra, which regex triggers need. unless your config already loads snippets from a directory, it also writes a small loader to `~/.config/nvim/plugin/snipsmith.lua` that turns on autosnippets. [vimtex](https://github.com/lervag/vimtex) is recommended for accurate math-context detection in latex files; without it snipsmith falls back to treesitter

it also asks where your `snippets.yaml` should live (default `~/.config/snipsmith/snippets.yaml`) and seeds it with the snippets from this repo if it doesn't exist yet. your answers are saved to `~/.config/snipsmith/config.toml`, which you can edit by hand or with `snipsmith config edit`. `snipsmith init --yes` accepts every default without prompting.

## everyday use

```
snipsmith build     # compile snippets.yaml and write every configured output
snipsmith edit      # open snippets.yaml in $EDITOR, then rebuild
snipsmith watch     # rebuild whenever snippets.yaml changes
snipsmith list sr   # show snippets matching "sr"
snipsmith doctor    # check editors, plugins, and whether outputs are up to date
snipsmith clean     # delete the generated files
```

`build` validates `snippets.yaml` first (unknown keys, invalid regexes, capture group references that don't exist, duplicate snippets, and more) and refuses to write anything if there are errors. `snipsmith build --diff` shows what would change without writing it.

that's pretty much it, enjoy! the `snippets.yaml` in this repo is what i actually use, if that's useful. it's a combination of the default obsidian-latex-suite snippets, snippets from [here](https://github.com/Einlar/latex_snippets/blob/master/hsnips/latex.hsnips), and my own personal snippets.

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

reference regex capture groups in replacements with obsidian-latex-suite's `[[n]]` syntax (`[[0]]` is the first capture group). snipsmith translates `[[n]]` into hypersnips' inline javascript form (` ``rv = m[n+1]`` `) for vscode and into a luasnip function node (`snip.captures[n+1]`) for neovim, so one replacement works on all platforms:

```yaml
snippets:
  - trigger: ([a-zA-Z])und
    replacement: "\\underline{[[0]]}"
    regex: true
    options:
      math: true
```

you only need a `platforms.vscode.replacement` or `platforms.neovim.replacement` override when that platform's version genuinely differs from a mechanical translation.

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

### visual snippets

snippets with `${VISUAL}` in their replacement wrap selected text (obsidian and neovim). in obsidian, select text and type the trigger. in neovim, select text, press `<Tab>` (the `store_selection_keys` mapping from setup), then type the trigger.

### multiple output paths

every platform in `config.toml` takes a list of paths, so one build can feed several vaults, both vscode and cursor, or latex and markdown at once. the file name picks the filetype for vscode (`latex.hsnips`, `markdown.hsnips`) and neovim (`tex.lua`, `markdown.lua`).

### platform-specific overrides

each snippet can have platform-specific overrides for obsidian, vscode, and neovim. this lets you customize triggers, replacements, or options per platform while keeping most of the snippet definition shared.

### shared variables

define variables once in the `variables` section and reference them in triggers and replacements using `{{VARIABLE_NAME}}`. snipsmith substitutes these automatically.
