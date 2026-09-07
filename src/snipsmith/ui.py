"""terminal output and prompting."""

import os
import sys

accept_defaults = False


def _paint(code: str, text: str, stream=sys.stdout) -> str:
    # legacy windows consoles do not interpret ansi; windows terminal and vscode do
    ansi_ok = os.name != "nt" or "WT_SESSION" in os.environ or "TERM_PROGRAM" in os.environ
    if ansi_ok and stream.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return text


def bold(text: str) -> str:
    return _paint("1", text)


def dim(text: str) -> str:
    return _paint("2", text)


def cyan(text: str) -> str:
    return _paint("36", text)


def heading(text: str) -> None:
    print(bold(text))


def ok(text: str) -> None:
    print(f"{_paint('32', '✓')} {text}")


def info(text: str) -> None:
    print(f"{dim('·')} {text}")


def _stderr(mark: str, text: str) -> None:
    sys.stdout.flush()
    print(f"{mark} {text}", file=sys.stderr)


def warn(text: str) -> None:
    _stderr(_paint("33", "!", sys.stderr), text)


def error(text: str) -> None:
    _stderr(_paint("31", "✗", sys.stderr), text)


def _prompt(text: str, fallback: str) -> str | None:
    """the user's answer, or None when the default should apply."""
    if accept_defaults or not sys.stdin.isatty():
        print(text + fallback)
        return None
    try:
        return input(text).strip()
    except EOFError:
        print()
        return None


def confirm(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    prompt = f"{cyan('?')} {question} {dim(f'[{hint}]')} "
    while True:
        answer = _prompt(prompt, "yes" if default else "no")
        if not answer:
            return default
        if answer.lower() in ("y", "yes"):
            return True
        if answer.lower() in ("n", "no"):
            return False
        print("  please answer y or n")


def ask(question: str, default: str | None = None) -> str:
    suffix = f" {dim(f'[{default}]')}" if default else ""
    prompt = f"{cyan('?')} {question}{suffix} "
    while True:
        answer = _prompt(prompt, default or "")
        if answer:
            return answer
        if answer is None or default is not None:
            return default or ""
        print("  a value is required")
