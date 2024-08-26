import copy
import functools
import importlib
import inspect
import json
import pkgutil
import sys
import typing
from enum import Enum
from http import HTTPStatus
from json import JSONDecodeError
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import cattrs
import click
from click import Argument, Context, Group
from click_didyoumean import DYMGroup
from httpx import UnsupportedProtocol
from plumbum.colors import blue, green, red, yellow  # noqa: F401

from openapi_cli import _client as client
from openapi_cli.config import CliConfig
from openapi_cli.helpers import client_is_installed, echo, get_script_name, print_validation_errors
from openapi_cli.interfaces.main import cli
from openapi_cli.symbols import BULLET, WARN

T = TypeVar("T")


F = typing.Callable[..., Any]
R = TypeVar("R")
P = ParamSpec("P")

TYPE_MAP = {
    str: click.STRING,
    int: click.INT,
    float: click.FLOAT,
    bool: click.BOOL,
}


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

        echo(result)

    return wrapper


def with_client(f, client_cls):
    """Initialize the API client."""

    @functools.wraps(f)
    def wrapper(ctx: Context, *args, **kwargs):
        script_name = get_script_name(ctx)
        error_msg = f"Use `{script_name} client api-config` to set the client base URL" | red
        try:
            return f(
                *args,
                **kwargs,
                client=get_api_client(client_cls),
            )
        except UnsupportedProtocol as e:
            echo(f"Got an error while connecting to the API: \n{e}" | red)
            raise click.UsageError(error_msg)
        except TypeError as e:
            raise click.UsageError(error_msg) from e

    return wrapper


def as_json(f: F, body_type: type) -> F:
    """Parse body as json."""

    @click.option("--json-file", type=Path, help="Input JSON file")
    @click.option("--json", "payload", type=str, help="JSON payload")
    @click.option("--edit", is_flag=True, help="Open text in editor")
    @functools.wraps(f)
    @click.pass_context
    def wrapper(
        ctx: Context,
        *args: P.args,
        json_file: Path | None = None,
        payload: str | None = None,
        edit: bool = False,
        **kwargs: P.kwargs,
    ) -> R:

        if not ctx.args and not json_file and not payload and not edit:
            echo(ctx.get_help())
            return

        if json_file is not None:
            with open(json_file, "r") as file:
                payload = file.read()

        if edit:
            payload = click.edit(payload, editor=ctx.obj.editor)

        if payload is not None:
            try:
                kwargs["body"] = client.CONVERTER.structure(json.loads(payload), body_type)
            except JSONDecodeError as e:
                raise click.UsageError(f"Invalid JSON payload: {e}" | red)
            except cattrs.errors.ClassValidationError as e:
                print_validation_errors(e)
                sys.exit(1)
        else:
            raise click.UsageError("JSON payload required" | red)

        return f(*args, **kwargs)

    w = WARN
    b = BULLET | yellow

    wrapper.__doc__ += "\b\n"
    wrapper.__doc__ += inspect.cleandoc(
        f"""
        {w} {"JSON payload required" | green} {w}
        {b} {"to pass a JSON payload use --json flag." | blue}
        {b} {"to pass a JSON file use --json-file flag." | blue}
        {b} {"to edit a JSON payload in a text editor use --edit flag." | blue}
    """
    )

    return wrapper


def add_to_click(func: T, value, name) -> T:
    """Add function as command to click."""

    name = name.replace("_", "-")

    value_type = TYPE_MAP.get(value.annotation, click.STRING)
    default_value = value.default

    is_list = False
    if isinstance(typing.get_origin(value.annotation), list):
        is_list = True

    if isinstance(typing.get_args(value.annotation), tuple):
        for arg in typing.get_args(value.annotation):
            orig = typing.get_origin(arg)
            if isinstance(orig, type) and issubclass(orig, list):
                is_list = True

    value_default = value.default

    if value_default == inspect.Parameter.empty and not is_list:
        func.__doc__ += f"{name}: {value_type}\n" | green
        func = click.argument(name)(func)
    else:
        func = click.option(
            f"--{name}",
            default=default_value,
            multiple=is_list,
            help=f"{name}" | blue,
            type=(
                click.Choice([e.value for e in value.annotation])
                if isinstance(value.annotation, Enum)
                else None
            ),
        )(func)

    return func


def setup_actions(config: CliConfig, module, group: Group) -> None:
    """Iterate over all API classes in a module."""

    for sub_module in pkgutil.iter_modules(module.__path__):
        sub_module_name = sub_module.name.replace("_", "-")
        if sub_module.ispkg:
            setup_actions(
                config,
                importlib.import_module(f"{module.__name__}.{sub_module.name}"),
                group.group(
                    sub_module_name,
                    help=f"Actions tagged with `{sub_module_name}` tag",
                    no_args_is_help=True,
                    invoke_without_command=True,
                    cls=DYMGroup,
                )(lambda: None),
            )
        else:
            sub_full_name = f"{module.__name__}.{sub_module.name}"

            sub_module = importlib.import_module(sub_full_name)
            func = getattr(sub_module, "sync_detailed")

            func.__doc__ = inspect.cleandoc(func.__doc__.split("Args:")[0])
            func.__doc__ = f"{func.__doc__}"
            func.__doc__ += "\n\nArguments:\n\n\b\n"

            if inspect.signature(func).parameters.get("client"):
                client_cls = inspect.signature(func).parameters.get("client").annotation
                func = with_client(func, client_cls)

            for name, value in inspect.signature(func).parameters.items():
                if name == "client":
                    continue

                elif name == "body":
                    func = as_json(func, value.annotation)

                else:
                    func = add_to_click(func, value, name)

            args_required = False
            if hasattr(func, "__click_params__"):
                args_required = bool([o for o in func.__click_params__ if isinstance(o, Argument)])

            cmd = group.command(sub_module_name, no_args_is_help=args_required)

            cmd(click.pass_context(print_result(func)))


@click.pass_obj
def get_api_client(config: CliConfig, client_cls: type[T] | tuple[type[T]]) -> T:
    """Get an API client instance."""

    if typing.get_origin(client_cls) is typing.Union:
        client_cls = typing.get_args(client_cls)[0]

    if isinstance(client_cls, tuple):
        client_cls = client_cls[0]

    if isinstance(client_cls, type):
        return client_cls(
            base_url=str(config.base_url),
            token=str(config.token),
        )


if client_is_installed():
    setup_actions(CliConfig.load(), client.api, cli)
