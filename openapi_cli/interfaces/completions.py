import inspect
import os
import time
from pathlib import Path

import click
from click import Context
from click_didyoumean import DYMGroup
from plumbum.cmd import cp
from plumbum.colors import blue, green, red, white, yellow  # noqa: F401

from openapi_cli.helpers import echo, get_script_name
from openapi_cli.interfaces.configure import configure
from openapi_cli.symbols import BACKUP, BAD, FILE, INFO, MAGNIFIER, OK, WRITE


@configure.group("completions", cls=DYMGroup)
def completions_group():
    """Terminal completion commands."""


def get_shell_info(script_name, shell="autodetect") -> tuple[Path, str, Path, str]:
    """Detect the current shell and source file and completion command."""

    supported_shells = ["bash", "zsh", "fish"]

    real_script_name = script_name
    script_name = script_name.replace("-", "_")
    command_name = script_name.upper()

    if shell == "autodetect":
        echo("Detecting shell..." | blue, MAGNIFIER)

        shell = os.environ.get("SHELL", "").split("/")[-1]

        echo(f"Detected shell: {shell}" | blue, OK if shell in supported_shells else BAD)

    rc_path = None
    rc_command = None
    script_rc_path = None

    if shell == "bash":
        rc_path = Path("~/.bashrc")
        script_rc_command = f'eval "$(_{command_name}_COMPLETE=zsh_source {real_script_name})"'
    elif shell == "zsh":
        rc_path = Path("~/.zshrc")
        script_rc_command = f'eval "$(_{command_name}_COMPLETE=zsh_source {real_script_name})"'
    elif shell == "fish":
        script_rc_path = Path(f"~/.config/fish/completions/{script_name}.fish")
        script_rc_command = f"_{command_name}_COMPLETE=fish_source {real_script_name} | source"
    else:
        raise click.UsageError(f"Unsupported shell {shell}" | red)

    script_rc_command = inspect.cleandoc(
        f"""
        # {real_script_name} completion
        if command -v {real_script_name} &>/dev/null; then {script_rc_command}; fi
        """
    )

    if shell in ["bash", "zsh"]:
        script_rc_path = Path(f"{rc_path}_{script_name}_completions")
        rc_command = f"source {script_rc_path}"

    return rc_path, rc_command, script_rc_path, script_rc_command


def create_rc_backup(rc_path: Path):
    """Create a backup of the shell configuration file."""

    backup_path = f"{rc_path}.backup{int(time.time())}"
    echo("Creating backup file..." | blue, BACKUP)
    cp[rc_path.expanduser(), backup_path]()
    echo(f"Shell configuration backed up to {backup_path}" | green, BACKUP)


@completions_group.command("enable")
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish", "autodetect"]),
    default="autodetect",
)
@click.pass_context
def enable_completions(ctx: Context, shell: str):
    """Generate bash completions for the CLI."""

    script_name = get_script_name(ctx)

    rc_path, rc_command, script_rc_path, script_rc_command = get_shell_info(
        script_name, shell=shell
    )

    echo(f"Creating completions script for `{script_name}`..." | blue, WRITE)

    with open(script_rc_path.expanduser(), "w") as f:
        f.write(script_rc_command)

    echo(f"Completions script created for `{script_name}` at {script_rc_path}" | green, FILE)

    if rc_path is not None and rc_command is not None:
        create_rc_backup(rc_path.expanduser())

        with open(rc_path.expanduser(), "r") as f:
            rc_text = f.read()

        if rc_command in rc_text:
            echo(f"Completions already enabled for `{script_name}` in {rc_path}" | yellow, OK)
        else:
            with open(rc_path.expanduser(), "a") as f:
                f.write(f"\n{rc_command}\n")

            echo(f"Completions enabled for {script_name}" | green, OK)

    help_msg = "To enable completions use `{cmd}`" | blue
    cmd = f"source {rc_path}" | white

    echo(f"{help_msg.format(cmd=cmd)}", INFO)


@completions_group.command("disable")
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish", "autodetect"]),
    default="autodetect",
)
@click.pass_context
def completions_disable(ctx: Context, shell: str):
    """Disable completions for the CLI."""

    rc_path, rc_command, script_rc_path, script_command = get_shell_info(
        ctx.parent.parent.info_name, shell=shell
    )

    if script_rc_path.exists():
        create_rc_backup(script_rc_path.expanduser())

    echo("Looking for completions in shell configuration..." | blue, MAGNIFIER)

    if rc_path is not None and rc_command is not None:
        create_rc_backup(rc_path.expanduser())

        with open(rc_path.expanduser(), "r") as rc_read_file:
            text = ""

            for line in rc_read_file.readlines():
                if script_command in line:
                    echo("Found completions command in shell configuration" | green, INFO)
                else:
                    text += line

            with open(rc_path.expanduser(), "w") as rc_write_file:
                rc_write_file.write(text)

    echo("Completions disabled in shell configuration" | green, OK)
