import os
import sys
from contextlib import contextmanager

import click
from cattrs import ClassValidationError
from click import Command, Context
from plumbum.colors import red  # noqa: F401

from openapi_cli.symbols import BAD, MAGNIFIER, QUESTION


def echo(text: str, prefix: str = ""):
    """Print text with a prefix."""

    click.echo(f"{f"{prefix} " if prefix else ""}{text}")


def confirm(text: str, default: bool = False) -> bool:
    """Confirm a message."""

    return click.confirm(f"{QUESTION} {text}", default=default)


def get_script_name(ctx: Context | None = None) -> str:
    """Get the script name from the context."""

    if ctx is None:
        return sys.argv[0].split("/")[-1]

    while ctx.parent is not None:
        ctx = ctx.parent

    return ctx.info_name


def print_validation_errors(exc: ClassValidationError) -> None:
    """Print validation errors."""

    echo(exc.args[0], MAGNIFIER)

    for error in exc.args[1:]:
        if isinstance(error, list):
            for sub_error in error:
                if isinstance(sub_error, KeyError):
                    echo(f"Missing required key: {sub_error}" | red, BAD)
                else:
                    echo(str(sub_error) | red, BAD)
        else:
            echo(str(error) | red, BAD)


@contextmanager
def patch(object_, attribute_name, value):
    """Patch an object attribute with a new value."""

    old_value = getattr(object_, attribute_name)
    try:
        setattr(object_, attribute_name, value)
        yield
    finally:
        setattr(object_, attribute_name, old_value)


def client_is_installed() -> bool:
    """Check if the client module is installed."""

    import openapi_cli._client as client

    try:
        return bool(client.api)
    except AttributeError:
        return False


def redirect(ctx: Context, cli: Command, command_path: str, *args, **kwargs):
    """Redirect to a subcommand."""

    command = cli
    for path in command_path.split("."):
        command = command.commands[path]

    ctx.invoke(command, *args, **kwargs)


def is_completions(command: str | None = ""):
    """Check if the script is running for completions."""

    comp_args = os.environ.get("COMP_WORDS")

    if comp_args is None:
        return False

    return command in comp_args


def is_command(command: str | None = None):
    """Check if the script is running for a specific command."""

    if len(sys.argv) < 2:
        return False

    return sys.argv[1] == command

def is_main_command():
    """Check if the script is running as the main command."""

    return len(sys.argv) == 1
