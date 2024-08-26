import click
from plumbum.colors import green  # noqa: F401

from openapi_cli.config import CliConfig
from openapi_cli.interfaces.main import cli
from openapi_cli.symbols import OK
from openapi_cli.helpers import echo


@cli.command("set-editor", no_args_is_help=True)
@click.argument("editor", type=str)
@click.pass_obj
def set_editor(config: CliConfig, editor: str):
    """Set the text editor to use for editing JSON.

    \b
    EDITOR: Text editor to use. Example: vim, nano, code.
    """

    config.editor = editor
    config.save()

    echo(f"Editor set to {editor}" | green, OK)
