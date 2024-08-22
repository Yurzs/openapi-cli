import copy
import functools
import importlib.util
import inspect
import json
import os
import pkgutil
import re
import sys
import typing
from enum import Enum
from functools import cached_property
from http import HTTPStatus
from json import JSONDecodeError, JSONEncoder
from pathlib import Path
from types import ModuleType
from typing import Any, ParamSpec, Self, TypeVar

import attrs
import click
from attr import AttrsInstance
from click import Argument, Context, Group, UsageError, pass_context
from plumbum import ProcessExecutionError
from pydantic import BaseModel, Field, HttpUrl
import plumbum.colors
from plumbum.cmd import grep, head, echo


T = TypeVar("T")

CONFIG_FOLDER = Path(".openapi_cli").absolute()
CONFIG_FILE = CONFIG_FOLDER.joinpath("config.json")

F = typing.Callable[..., Any]
R = TypeVar("R")
P = ParamSpec("P")

TYPE_MAP = {
    str: click.STRING,
    int: click.INT,
    float: click.FLOAT,
    bool: click.BOOL,
}


class CliConfig(BaseModel):
    """CLI configuration file model."""

    client_module_name: Path | str | None = Field(
        None, description="Python module containing the " "client"
    )
    base_url: HttpUrl | None = Field(None, description="Base URL of the API")
    token: str | None = Field(None, description="API token")

    @classmethod
    def load(cls) -> Self:
        if not CONFIG_FILE.exists():
            return cls()

        with open(CONFIG_FILE, "r") as f:
            return cls.model_validate_json(f.read())

    def save(self):
        """Save the configuration to disk."""

        CONFIG_FOLDER.mkdir(exist_ok=True, parents=True)
        CONFIG_FILE.write_text(
            self.model_dump_json(
                by_alias=True,
                exclude_none=True,
                exclude={"state"},
            )
        )

    @cached_property
    def client_models(self) -> ModuleType:
        return importlib.import_module(f"{self.client_module_name}.models")

    @cached_property
    def client_types(self) -> ModuleType:
        return importlib.import_module(f"{self.client_module_name}.types")

    @cached_property
    def json_encoder(self) -> type[JSONEncoder]:
        def default(encoder, obj):
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, self.client_types.Unset):
                return None
            return obj

        return type("JSONEncoder", (JSONEncoder,), {"default": default})


@click.group(no_args_is_help=True, invoke_without_command=True)
@pass_context
def cli(ctx: Context):
    ctx.obj = CliConfig.load()

    module_err = (
        f"Use `{ctx.info_name} client install` to set the client module first!" | plumbum.colors.red
    )

    if ctx.obj.client_module_name is None and ctx.invoked_subcommand != "client":
        raise click.UsageError(module_err)


@cli.group("client")
def client_group():
    pass


@cli.group(
    "action",
    help="List of API actions",
    invoke_without_command=True,
    no_args_is_help=True,
)
@click.pass_obj
def action_group(config: CliConfig):
    pass


def print_result(f: F) -> F:
    """Print the result of the function."""

    def list_items_to_dict(items: list) -> list:
        result = []

        for item in items:
            if hasattr(item, "to_dict"):
                result.append(item.to_dict())
            else:
                result.append(item)

        return result

    @functools.wraps(f)
    @click.pass_obj
    def wrapper(config: CliConfig, *args: P.args, **kwargs: P.kwargs) -> R:
        """Print the result of the function."""

        orig_result = f(*args, **kwargs)
        result = copy.deepcopy(orig_result)
        if result is None:
            return

        if (
            hasattr(result, "status_code")
            and getattr(result, "parsed", None) is None
            and not getattr(result, "content", None)
        ):
            status: HTTPStatus = result.status_code
            result = f"{status.value} {status.name}: {status.description}"

        if getattr(result, "parsed", None) is not None:
            result = result.parsed

        elif getattr(result, "content") is not None:
            try:
                result = json.loads(result.content)
            except json.JSONDecodeError:
                result = f"{orig_result.status_code}: {result.content.decode()}"

        if isinstance(result, list):
            result = list_items_to_dict(result)

        if hasattr(result, "to_dict"):
            result = result.to_dict()

        result = json.dumps(result, indent=2, cls=config.json_encoder)

        click.echo(result)

    return wrapper


def with_client(f, client_cls):
    """Initialize the API client."""

    @functools.wraps(f)
    def wrapper(_: Context, *args, **kwargs):
        try:
            return f(
                *args,
                **kwargs,
                client=get_api_client(client_cls),
            )
        except TypeError as e:
            raise click.UsageError(f"Use `configure` to set the client url and token: {e}")

    return wrapper


def as_json(f: F, model: type[AttrsInstance]) -> F:
    """Parse body as json."""

    @click.option("--json-file", type=Path, help="Input JSON file")
    @click.option("--json", "payload", type=str, help="JSON payload")
    @click.option("--json-edit", type=str, help="Open text editor name.", default=False)
    @functools.wraps(f)
    @click.pass_context
    def wrapper(
        ctx: Context,
        *args: P.args,
        json_file: Path | None = None,
        payload: str | None = None,
        json_edit: str | None = False,
        **kwargs: P.kwargs,
    ) -> R:

        if not ctx.args and not json_file and not payload and not json_edit:
            click.echo(ctx.get_help())
            return

        if json_file is not None:
            with open(json_file, "r") as file:
                payload = file.read()

        if json_edit:
            payload = click.edit(payload, editor=json_edit)

        if payload is not None:
            try:
                kwargs["body"] = model.from_dict(json.loads(payload))
            except JSONDecodeError as e:
                raise click.UsageError(f"Invalid JSON payload: {e}")
            except KeyError as e:
                raise click.UsageError(f"Missing required key: {e}")

        return f(*args, **kwargs)

    return wrapper


def add_to_click(config: CliConfig, func: T, value, name) -> T:
    """Add function as command to click."""

    name = name.replace("_", "-")

    value_type = TYPE_MAP.get(value.annotation, click.STRING)
    default_value = (
        value.default if not isinstance(value.default, config.client_types.Unset) else None
    )

    is_list = False
    if isinstance(typing.get_origin(value.annotation), list):
        is_list = True

    if isinstance(typing.get_args(value.annotation), tuple):
        for arg in typing.get_args(value.annotation):
            orig = typing.get_origin(arg)
            if isinstance(orig, type) and issubclass(orig, list):
                is_list = True

    value_default = value.default

    if isinstance(value.annotation, type) and attrs.has(value.annotation):
        value_type = "JSON (call `--show-schema` for more info)"
        schema = getattr(config.client_models, value.annotation.__name__).model_json_schema()
        value_default = None
        func = as_json(func, schema)

    if value_default == inspect.Parameter.empty and not is_list:
        func.__doc__ += f"\b{name}: {value_type}"
        func = click.argument(name)(func)
    else:
        func = click.option(
            f"--{name}",
            default=default_value,
            multiple=is_list,
            help=f"{name}",
            type=(
                click.Choice([e.value for e in value.annotation])
                if isinstance(value.annotation, Enum)
                else None
            ),
        )(func)

    return func


def iter_api(config: CliConfig, module: str, group: Group) -> None:
    """Iterate over all API classes in a module."""

    module = importlib.import_module(module)
    for sub_module in pkgutil.iter_modules(module.__path__):
        sub_module_name = sub_module.name.replace("_", "-")
        if sub_module.ispkg:
            iter_api(
                config,
                f"{module.__name__}.{sub_module.name}",
                group.group(
                    sub_module_name,
                    help=f"Actions tagged with `{sub_module_name}` tag",
                    no_args_is_help=True,
                    invoke_without_command=True,
                )(lambda: None),
            )
        else:
            full_name = f"{module.__name__}.{sub_module.name}"

            func = getattr(importlib.import_module(full_name), "sync_detailed")

            func.__doc__ = func.__doc__.split("\n")[0] + "\n\n"

            if inspect.signature(func).parameters.get("client"):
                client_cls = inspect.signature(func).parameters.get("client").annotation
                func = with_client(func, client_cls)

            for name, value in inspect.signature(func).parameters.items():
                if name == "client":
                    continue

                elif name == "body" and attrs.has(value.annotation):
                    model = getattr(config.client_models, value.annotation.__name__)
                    func = as_json(func, model)

                else:
                    func = add_to_click(config, func, value, name)

            args_required = False
            if hasattr(func, "__click_params__"):
                args_required = bool([o for o in func.__click_params__ if isinstance(o, Argument)])

            cmd = group.command(sub_module_name, no_args_is_help=args_required)

            cmd(click.pass_context(print_result(func)))


@click.pass_obj
def get_api_client(config: CliConfig, client_cls: type[T] | tuple[type[T]]) -> T:
    """Get an API client instance."""

    if isinstance(client_cls, tuple):
        client_cls = client_cls[0]

    if isinstance(client_cls, type):
        return client_cls(
            base_url=str(config.base_url),
            token=str(config.token),
        )


def validate_client_module(config: CliConfig) -> bool:
    """Validate that the client module exists and has all the necessary submodules."""

    required_submodules = ["api", "models", "client", "errors", "types"]

    for submodule in required_submodules:
        try:
            importlib.import_module(f"{config.client_module_name}.{submodule}")
        except (AttributeError, ModuleNotFoundError) as e:
            raise click.UsageError(str(e) | plumbum.colors.red) from None

    return True


@client_group.command("configure", no_args_is_help=True)
@click.option(
    "--client-module",
    help="Client module name. Example: 'fast_api_client'",
)
@click.option("--base-url", help="Base API URL")
@click.pass_obj
def configure(
    config: CliConfig,
    client_module: str | None = None,
    base_url: HttpUrl | None = None,
) -> None:
    """Configure the Open API CLI to use a specific client module.

    \b
    CLIENT_MODULE: generated module by `openapi-python-client`.
    """

    if config.client_module_name is None and client_module is None:
        raise click.UsageError(
            f"{config.client_module_name} is not set and no client module was provided"
        )

    elif client_module is not None:
        config.client_module = client_module
        validate_client_module(config)

    if base_url is not None:
        config.base_url = base_url

    config.save()

    click.echo("Client module configured successfully")


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
    {"Git URL to the client module" | plumbum.colors.green}
    {"[add --module if the package is a submodule]" | plumbum.colors.blue}
"""


@client_group.command("install", no_args_is_help=True)
@click.option("--module", type=str, help="Module name to install" | plumbum.colors.green)
@click.option("--git", help=GIT_URL_HELP)
@click.pass_obj
def install_client(
    config: CliConfig,
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
            if not click.confirmation_option(
                prompt="Install in system Python?" | plumbum.colors.warn,
                default=False,
            ):
                return click.echo("Aborted")

        install_cmd = install_cmd[f"git+{git}"]
    else:
        raise click.UsageError("Provide either a module name or git URL" | plumbum.colors.red)

    if install_cmd is not None:
        try:
            result = install_cmd()
        except ProcessExecutionError as e:
            click.echo(e, color=True)
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
            message = f"""
                {"Failed to find the client module name: {e.message}\n" | plumbum.colors.red}
                {"If the client package is under different name specify it with --module" | plumbum.colors.yellow}
            """

            click.echo(message, color=True)
            return
        else:
            raise e

    click.echo("Client module installed successfully" | plumbum.colors.green, color=True)
    config.save()


@cli.group("completions")
def completions_group():
    pass


@completions_group.command("enable")
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish", "autodetect"]),
    default="autodetect",
)
@click.pass_context
def enable_completions(ctx: Context, shell: str):
    """Generate bash completions for the CLI."""

    if shell == "autodetect":
        shell = os.environ.get("SHELL", "").split("/")[-1]

    script_name = ctx.parent.parent.info_name
    command_name = script_name.upper().replace("-", "_")

    if shell == "bash":
        file_path = "~/.bashrc"
        command = f'eval "$(_{command_name}_COMPLETE=zsh_source {script_name})"'
    elif shell == "zsh":
        file_path = "~/.zshrc"
        command = f'eval "$(_{command_name}_COMPLETE=zsh_source {script_name})"'
    elif shell == "fish":
        file_path = f"~/.config/fish/completions/{script_name}.fish"
        command = f"_{command_name}_COMPLETE=fish_source {script_name} | source"
    else:
        raise click.UsageError(f"Invalid shell {shell}" | plumbum.colors.red)

    action = echo[command] >> str(Path(file_path).expanduser())
    action()

    click.echo(f"Completions enabled for {shell}" | plumbum.colors.green)


def main():
    return cli()


# Add API actions to the CLI completions
__conf = CliConfig.load()
if __conf.client_module_name is not None:
    iter_api(__conf, f"{__conf.client_module_name}.api", action_group)

if __name__ == "__main__":
    cli()
