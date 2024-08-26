from click_didyoumean import DYMGroup

from openapi_cli.interfaces.main import cli


@cli.group("configure", no_args_is_help=True, invoke_without_command=True, cls=DYMGroup)
def configure():
    """Configure the OpenAPI CLI tool."""
