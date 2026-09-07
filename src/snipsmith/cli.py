"""command line entry point."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from . import __version__, editors, init, ui
from .build import plan, render_diff, write
from .compiler import (
    PLATFORMS,
    SnippetError,
    SnippetSource,
    load_source,
    merge_for_platform,
    obsidian_flags,
    targets_platform,
)
from .config import Config, collapse, config_file, expand
from .errors import CliError


def resolve_source(cfg: Config, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    if cfg.snippets:
        return cfg.snippets
    if Path("snippets.yaml").exists():
        return Path("snippets.yaml")
    raise CliError("no snippets.yaml configured; run `snipsmith init` or pass a path")


def load(path: Path) -> SnippetSource:
    source = load_source(path)
    for warning in source.warnings:
        ui.warn(warning)
    return source


def open_in_editor(path: Path) -> int:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if os.name == "nt":
        status = subprocess.call(f'{editor} "{path}"', shell=True)
    else:
        status = subprocess.call([*shlex.split(editor), str(path)])
    if status != 0:
        ui.error(f"{editor} exited with status {status}")
        return 1
    return 0


def platform_list(value: str) -> list[str]:
    names = [v.strip() for v in value.split(",") if v.strip()]
    for name in names:
        if name not in PLATFORMS:
            raise argparse.ArgumentTypeError(
                f"unknown platform '{name}' (choose from {', '.join(PLATFORMS)})"
            )
    return names


def build(cfg: Config, source_path: Path, out_dir: Path | None, dry_run: bool = False) -> int:
    source = load(source_path)
    planned = plan(source, cfg, out_dir)
    for item in planned:
        if not item.changed:
            ui.info(f"unchanged {collapse(item.path)}")
            continue
        if not dry_run:
            write(item)
        ui.ok(("would write " if dry_run else "wrote ") + collapse(item.path))
    changed = sum(item.changed for item in planned)
    print(f"\n{len(source.snippets)} snippets → {len(planned)} files, {changed} changed")
    return 0


def cmd_init(args) -> int:
    ui.accept_defaults = args.yes
    cfg = init.run(args.only, args.skip)
    if cfg.has_destinations() and cfg.snippets and ui.confirm("build snippets now?"):
        print()
        return build(cfg, cfg.snippets, None)
    ui.info("run `snipsmith build` whenever you change your snippets")
    return 0


def cmd_build(args) -> int:
    cfg = Config.load(args.config)
    source_path = resolve_source(cfg, args.snippets)
    if args.diff:
        print(render_diff(plan(load(source_path), cfg, args.out)) or "no changes")
        return 0
    return build(cfg, source_path, args.out, args.dry_run)


def cmd_check(args) -> int:
    cfg = Config.load(args.config)
    source_path = resolve_source(cfg, args.snippets)
    source = load(source_path)
    ui.ok(f"validated {len(source.snippets)} snippets in {collapse(source_path)}")
    return 0


def cmd_clean(args) -> int:
    cfg = Config.load(args.config)
    paths = [p for p in cfg.all_paths() if p.exists()]
    if not paths:
        ui.info("nothing to clean")
        return 0
    for path in paths:
        print(f"  {collapse(path)}")
    if not args.yes and not ui.confirm(
        f"remove these {len(paths)} generated file(s)?", default=False
    ):
        return 0
    for path in paths:
        path.unlink()
        ui.ok(f"removed {collapse(path)}")
    return 0


def cmd_edit(args) -> int:
    cfg = Config.load(args.config)
    source_path = resolve_source(cfg, None)
    if open_in_editor(source_path):
        return 1
    if args.no_build or not cfg.has_destinations():
        return 0
    print()
    return build(cfg, source_path, None)


def cmd_watch(args) -> int:
    cfg = Config.load(args.config)
    source_path = resolve_source(cfg, args.snippets)
    ui.info(f"watching {collapse(source_path)} (ctrl-c to stop)")
    last: float | None = None
    while True:
        try:
            mtime = source_path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != last and mtime is not None:
            print()
            try:
                build(cfg, source_path, args.out)
            except SnippetError as e:
                report_source_problem(e)
        last = mtime
        time.sleep(0.5)


def cmd_doctor(args) -> int:
    cfg = Config.load(args.config)
    problems = 0

    ui.heading("config")
    if config_file().exists():
        ui.ok(collapse(config_file()))
    else:
        ui.warn("no config file; run `snipsmith init`")
        problems += 1
    if cfg.snippets and not cfg.snippets.exists():
        ui.error(f"snippets file missing: {collapse(cfg.snippets)}")
        problems += 1
    elif cfg.snippets:
        ui.ok(f"snippets: {collapse(cfg.snippets)}")
    print()

    editors.report(editors.detect_all(set(PLATFORMS)))

    ui.heading("outputs")
    if not cfg.has_destinations():
        ui.warn("no output paths configured")
        problems += 1
    elif cfg.snippets and cfg.snippets.exists():
        planned = plan(load(cfg.snippets), cfg)
        for item in planned:
            if item.current is None:
                ui.error(f"missing {collapse(item.path)}")
            elif item.changed:
                ui.warn(f"stale {collapse(item.path)}")
            else:
                ui.ok(f"up to date {collapse(item.path)}")
        problems += sum(item.changed for item in planned)
        if any(item.changed for item in planned):
            ui.info("run `snipsmith build` to refresh")
    print()
    if problems:
        ui.warn(f"{problems} problem(s) found")
        return 1
    ui.ok("everything looks good")
    return 0


def cmd_list(args) -> int:
    cfg = Config.load(args.config)
    source = load(resolve_source(cfg, args.snippets))
    platform = args.platform or "obsidian"
    query = (args.query or "").lower()
    rows = []
    for snippet in source.snippets:
        if args.platform and not targets_platform(snippet, args.platform):
            continue
        merged = merge_for_platform(snippet, platform, source.default_options)
        description = merged.get("description", "")
        haystack = f"{merged['trigger']} {merged['replacement']} {description}".lower()
        if query not in haystack:
            continue
        rows.append(
            (
                merged["trigger"],
                obsidian_flags(merged),
                merged["replacement"].replace("\n", "⏎"),
                description,
            )
        )
    if not rows:
        ui.info("no matching snippets")
        return 0
    trigger_w = min(max(len(r[0]) for r in rows), 28)
    repl_w = min(max(len(r[2]) for r in rows), 40)
    for trigger, flags, replacement, description in rows:
        print(
            f"{trigger[:trigger_w]:<{trigger_w}}  {flags:<5} "
            f"{replacement[:repl_w]:<{repl_w}}  {ui.dim(description)}"
        )
    print(f"\n{len(rows)} snippets")
    return 0


def cmd_config(args) -> int:
    path = args.config or config_file()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "edit":
        if not path.exists():
            Config().save(path)
        return open_in_editor(path)
    if not path.exists():
        ui.warn(f"no config at {collapse(path)}; run `snipsmith init`")
        return 1
    print(ui.dim(f"# {path}"))
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def report_source_problem(e: SnippetError) -> None:
    for warning in e.warnings:
        ui.warn(warning)
    for error in e.errors:
        ui.error(error)
    ui.error(f"validation failed with {len(e.errors)} error(s); nothing was written")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snipsmith",
        description="manage latex snippets for obsidian, vscode, and neovim from one snippets.yaml",
    )
    parser.add_argument("-V", "--version", action="version", version=f"snipsmith {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        type=expand,
        metavar="FILE",
        help="config file (default: ~/.config/snipsmith/config.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    snippets_arg = argparse.ArgumentParser(add_help=False)
    snippets_arg.add_argument(
        "snippets", nargs="?", type=expand, help="snippets.yaml to read (default: from config)"
    )
    out_arg = argparse.ArgumentParser(add_help=False)
    out_arg.add_argument(
        "-o",
        "--out",
        type=expand,
        metavar="DIR",
        help="also write a canonical copy of each file here",
    )

    p = sub.add_parser(
        "init", help="detect your editors, install their snippet plugins, and write a config"
    )
    p.add_argument(
        "-y", "--yes", action="store_true", help="accept every default without prompting"
    )
    p.add_argument(
        "--only",
        type=platform_list,
        metavar="PLATFORMS",
        help="comma-separated subset of obsidian,vscode,neovim to set up",
    )
    p.add_argument(
        "--skip",
        type=platform_list,
        metavar="PLATFORMS",
        help="comma-separated platforms to leave alone",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "build",
        parents=[snippets_arg, out_arg],
        help="compile snippets.yaml and write every configured output",
    )
    p.add_argument(
        "-n", "--dry-run", action="store_true", help="report what would change without writing"
    )
    p.add_argument(
        "--diff", action="store_true", help="show a unified diff against the current files"
    )
    p.set_defaults(func=cmd_build)

    p = sub.add_parser(
        "check", parents=[snippets_arg], help="validate snippets.yaml without writing anything"
    )
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("clean", help="delete the generated snippet files")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("edit", help="open snippets.yaml in $EDITOR, then rebuild")
    p.add_argument("--no-build", action="store_true", help="only edit, do not rebuild afterwards")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser(
        "watch", parents=[snippets_arg, out_arg], help="rebuild whenever snippets.yaml changes"
    )
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser(
        "doctor", help="check editors, plugins, config, and whether outputs are current"
    )
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("list", help="list snippets, optionally filtered")
    p.add_argument(
        "query", nargs="?", help="substring to match in trigger, replacement, or description"
    )
    p.add_argument(
        "-p", "--platform", choices=PLATFORMS, help="show the snippet as this platform sees it"
    )
    p.add_argument(
        "-f",
        "--file",
        dest="snippets",
        type=expand,
        metavar="SNIPPETS",
        help="snippets.yaml to read (default: from config)",
    )
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("config", help="show, locate, or edit the config file")
    p.add_argument("action", nargs="?", choices=("show", "path", "edit"), default="show")
    p.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        sys.exit(args.func(args))
    except SnippetError as e:
        report_source_problem(e)
        sys.exit(1)
    except CliError as e:
        ui.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
