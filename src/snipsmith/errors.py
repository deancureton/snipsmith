class CliError(Exception):
    """a user-facing failure; the message is printed and the command exits 1."""


class EditorError(Exception):
    """an editor plugin step failed; the message is shown and setup continues."""
