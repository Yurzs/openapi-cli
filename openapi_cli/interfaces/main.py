from importlib.metadata import version as get_version

import click
from click import Context, pass_context
from click_didyoumean import DYMGroup
from plumbum.colors import red  # noqa: F401

from openapi_cli.config import CliConfig
from openapi_cli.helpers import echo


@click.group(cls=DYMGroup, no_args_is_help=True, invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show openapi-cli version")
@pass_context
def cli(ctx: Context, version: bool = False):
    """OpenAPI CLI tool."""

    if version:
        echo(f"{get_version("openapi-cli")}")
        return

    ctx.obj = CliConfig.load()

    module_err = f"Use `{ctx.info_name} client install` to set the client module first!" | red

    if ctx.obj.client_module_name is None and ctx.invoked_subcommand != "client":
        raise click.UsageError(module_err)
