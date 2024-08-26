import importlib
import inspect
import os
import re
import sys
from pathlib import Path

import click
from click import Context, UsageError
from click_didyoumean import DYMGroup
from plumbum import ProcessExecutionError
from plumbum.cmd import grep, head, mv, rm, ruff
from plumbum.colors import blue, green, red, yellow  # noqa: F401
from pydantic import HttpUrl

from openapi_cli.config import CliConfig
from openapi_cli.helpers import confirm, echo, redirect
from openapi_cli.interfaces.configure import configure
from openapi_cli.interfaces.main import cli
from openapi_cli.patcher import patch_submodule
from openapi_cli.separator import CLI_SEPARATOR
from openapi_cli.symbols import BAD, CLEAN, MOVE, OK, WARN, WRITE


@configure.group("client", cls=DYMGroup)
def client_group():
    """Client configuration commands."""


def validate_client_module(config: CliConfig) -> bool:
    """Validate that the client module exists and has all the necessary submodules."""

    required_submodules = ["api", "models", "client", "errors", "types"]

    for submodule in required_submodules:
        try:
            importlib.import_module(f"{config.client_module_name}.{submodule}")
        except (AttributeError, ModuleNotFoundError) as e:
            raise click.UsageError(str(e) | red) from None

    return True


@client_group.command("api-config", no_args_is_help=True)
@click.option("--base-url", help="Base API URL")
@click.pass_obj
def configure(
    config: CliConfig,
    base_url: HttpUrl | None = None,
) -> None:
    """Configure basic OpenAPI Client options.

    \b
    BASE_URL: Base URL of the API.
    """

    if base_url is not None:
        config.base_url = HttpUrl(base_url)

    config.save()

    echo("Client module configured successfully" | green, OK)


@client_group.command(
    "auth",
    no_args_is_help=True,
)
@click.argument("token", type=str)
@click.pass_obj
def auth(config: CliConfig, token: str) -> None:
    """Authenticate the user with a token.

    \b
    TOKEN: API token.

    """

    config.token = token
    config.save()


GIT_URL_HELP = f"""
    \b
    {"Git URL to the client module" | green}
    {"[add --module if the package is a submodule]" | blue}
"""


@client_group.command("install", no_args_is_help=True)
@click.option("--module", type=str, help="Module name to install" | green)
@click.option("--git", help=GIT_URL_HELP)
@click.pass_context
def install_client(
    ctx: Context,
    module: str | None,
    git: str | None,
):
    """Install a client module from git URL or module name.

    \b
    You can install the client module from a git URL or a module name.
    If you provide a module name, the module will be installed from PyPI.
    If you provide a git URL, the module will be installed from the git repository.
    If the client module is a submodule, provide the module name with --module.
    """

    config: CliConfig = ctx.obj

    try:
        from plumbum.cmd import poetry

        pip = poetry["run", "pip"]
    except ImportError:
        from plumbum.cmd import pip

    install_cmd = pip["install"]

    if module is not None and git is None:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            install_cmd = install_cmd[module]
        else:
            config.client_module_name = module
            install_cmd = None

    elif git is not None:
        if sys.prefix == sys.base_prefix:
            if not confirm(
                "Install in system Python?" | yellow,
                default=False,
            ):
                return echo("Aborted" | yellow, WARN)

        install_cmd = install_cmd[f"git+{git}"]
    else:
        raise click.UsageError("Provide either a module name or git URL" | red)

    if install_cmd is not None:
        try:
            result = install_cmd()
        except ProcessExecutionError as e:
            echo(str(e) | red, BAD)
            return
        else:
            result = (grep["(from"] << result)()
            result = (head["-n", 1] << result)()

            if module is not None:
                config.client_module_name = module
            else:
                config.client_module_name = re.findall(r"\(from (?P<module>.*)==", result)[
                    0
                ].replace("-", "_")

    try:
        validate_client_module(config)
    except UsageError as e:
        if module is None:
            message = inspect.cleandoc(
                f"""
                {"Failed to find the client module name: {e.message}\n" | red}
                {"If the client package is under different name specify it with --module" | yellow}
            """
            )

            echo(message, BAD)
            return
        else:
            raise e

    echo("Client module installed successfully" | green, OK)
    config.save()


@client_group.command("patch")
@click.option("--separator", help="Separator for nested commands")
@click.pass_obj
def patch_client(config: CliConfig, separator: str | None = None):
    """Patch client generated with openapi-python-client to support more nested commands."""

    separator = separator or CLI_SEPARATOR

    patch_submodule(f"{config.client_module_name}.api", separator)

    echo("Client patched successfully" | green, OK)


@client_group.command("generate", no_args_is_help=True)
@click.argument("api-url", type=str)
@click.argument("output", type=Path, default="{your_cli}_client")
@click.pass_context
def generate_client(ctx: Context, api_url: str, output: Path):
    """Generate a client module from an OpenAPI schema.

    WARNING: This will overwrite the existing client module completely.

    \b
    API_URL: URL to the OpenAPI schema. Example: "http://localhost:8000/openapi.json",
    OUTPUT: Output folder name. Default: "{your-cli}_client".
    """

    output = Path(
        str(output).format(your_cli=ctx.parent.parent.info_name.lower()).replace("-", "_")
    )

    try:
        from plumbum.cmd import openapi_python_client
    except ImportError:
        try:
            from plumbum.cmd import poetry

            openapi_python_client = poetry["run", "openapi-python-client"]
        except ImportError:
            raise click.UsageError("openapi-python-client is not installed" | red)

    tmp_client_path = Path(f"/tmp/openapi_client_{os.urandom(5).hex()}")

    echo("Generating client..." | blue, WRITE)

    try:
        openapi_python_client[
            "generate",
            "--url",
            api_url,
            "--overwrite",
            "--output-path",
            tmp_client_path,
        ]()
    except Exception as e:
        raise click.UsageError(f"Failed to generate client: {e}" | red)

    folder_signature = {
        "client.py",
        "__init__.py",
        "types.py",
        "models",
        "py.typed",
        "api",
        "errors.py",
    }

    inner_client_path = None

    # Find client folder name
    for fs_entity in tmp_client_path.iterdir():
        if fs_entity.is_dir():
            if not folder_signature.difference([f.name for f in fs_entity.iterdir()]):
                inner_client_path = fs_entity
                break
    else:
        raise click.UsageError("Unable to find python module.")

    echo("Cleaning up old client..." | blue, CLEAN)
    rm["-rf", output]()
    rm["-rf", f"/tmp/{inner_client_path.name}"]()

    echo("Moving new client..." | blue, MOVE)
    mv[inner_client_path, f"/tmp/"]()
    mv[f"/tmp/{inner_client_path.name}", output]()

    echo("Cleaning up tmp files..." | blue, CLEAN)
    rm["-rf", tmp_client_path]()

    echo("Removing relative imports...", CLEAN)
    ruff["check", "--select", "TID252", "--unsafe-fixes", "--fix", "--fix-only", output]()
    echo(f"Client generated at {output}" | green, OK)

    if confirm("Do you want to install the client?" | green, default=True):
        ctx.invoke(install_client, module=output.name)

    if confirm("Do you want to apply patches?" | green, default=True):
        ctx.invoke(
            patch_client,
        )

    url = HttpUrl(api_url)

    if confirm("Do you want to save this url as api base?" | green, default=True):
        port = url.port
        if (
            port is not None
            and port not in [80, 443]
            and not confirm(f"Is port {port} correct?" | yellow, default=True)
        ):
            port = None
            not_correct_port = True

            while not_correct_port:
                port = click.prompt("Enter the correct port" | blue, type=int)
                if port < 0 or port > 65535:
                    echo("Port must be between 0 and 65535" | red, BAD)
                    continue
                break

            api_url = api_url.replace(str(url.port), str(port))

        ctx.invoke(configure, base_url=api_url.replace("/openapi.json", ""))

    if confirm("Do you want to enable completions?" | green, default=True):
        redirect(ctx, cli, "configure.completions.enable", shell="autodetect")
