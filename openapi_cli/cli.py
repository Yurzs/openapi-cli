import copy
import functools
import importlib.util
import inspect
import json
import pkgutil
import typing
from enum import Enum
from http import HTTPStatus
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Self, TypeVar

import attrs
import click
from click import Command, Context, Group
from pydantic import BaseModel, Field, HttpUrl

T = TypeVar("T")

CONFIG_FOLDER = Path(".openapi_cli").absolute()
CONFIG_FILE = CONFIG_FOLDER.joinpath("config.json")


class APIMode(str, Enum):
    default = "default"
    json = "json"


class CliConfig(BaseModel):
    """CLI configuration file model."""

    client_module: Annotated[
        str | None,
        Field(None, description="Python module containing the " "client"),
    ]
    base_url: Annotated[HttpUrl | None, Field(None, description="Base URL of the API")]
    token: Annotated[str | None, Field(None, description="API token")]

    @classmethod
    def load(cls) -> Self:
        if not CONFIG_FILE.exists():
            return cls()

        with open(CONFIG_FILE, "r") as f:
            return cls.model_validate_json(f.read())

    def save(self):
        """Save the configuration to disk."""

        CONFIG_FOLDER.mkdir(exist_ok=True, parents=True)
        CONFIG_FILE.write_text(self.model_dump_json(by_alias=True, exclude_none=True))


@click.group()
def cli():
    return


class MetaCli(type):

    TYPE_MAP = {
        str: click.STRING,
        int: click.INT,
        float: click.FLOAT,
        bool: click.BOOL,
    }

    def __init__(cls, name: str, bases: tuple[Any, ...], namespace: dict[str, Any]):
        cls.add_self_to_static_commands(namespace)
        cls.setup_commands(namespace)
        super().__init__(name, bases, namespace)

    def cls_with_self(cls, f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if "self" in f.__code__.co_varnames:
                return f(cls(), *args, **kwargs)

            return f(*args, **kwargs)

        return wrapper

    @staticmethod
    def with_optional_self(f, self, *args, **kwargs):
        if "self" not in f.__code__.co_varnames:
            return f(*args, **kwargs)
        else:
            return f(self, *args, **kwargs)

    @staticmethod
    def print_result(f):

        def list_items_to_dict(items: list) -> list:
            result = []

            for item in items:
                if hasattr(item, "to_dict"):
                    result.append(item.to_dict())
                else:
                    result.append(item)

            return result

        @functools.wraps(f)
        def wrapper(ctx, *args, **kwargs):
            orig_result = f(ctx, *args, **kwargs)
            result = copy.deepcopy(orig_result)

            if attrs.has(type(result)) and attrs.asdict(result):
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

            click.echo(json.dumps(result, indent=2, sort_keys=True), color=True)

        return wrapper

    def with_client(cls, f, client_cls):

        @functools.wraps(f)
        def wrapper(self: "OpenAPICli", *args, **kwargs):
            try:
                return cls.with_optional_self(
                    f,
                    self,
                    *args,
                    **kwargs,
                    client=self.get_api_client(client_cls),
                )
            except TypeError as e:
                raise click.UsageError(f"Use `configure` to set the client url and token: {e}")

        return wrapper

    def with_transform_body(cls, f, body_cls):
        @functools.wraps(f)
        def wrapper(self: "OpenAPICli", *args, **kwargs):

            body_kwargs = {}

            for key, value in inspect.signature(body_cls).parameters.items():
                if isinstance(value.annotation, str):
                    kwargs[key] = json.loads(kwargs.get(key, {}))

                if value.default == inspect.Parameter.empty:
                    body_kwargs[key] = kwargs.pop(key)
                else:
                    body_kwargs[key] = kwargs.pop(key, value.default)

            try:
                body = body_cls.from_dict(body_kwargs)
            except TypeError as e:
                if "missing" in str(e):
                    arg_name = str(e).split("'")[1]
                    raise click.UsageError(f"Missing required argument: {arg_name}")
                raise click.UsageError(str(e))

            kwargs["body"] = body

            return cls.with_optional_self(f, self, *args, **kwargs)

        return wrapper

    @staticmethod
    def final_wrapper(f):
        @functools.wraps(f)
        def wrapper(ctx: Context, *args, **kwargs):
            print(ctx.args)
            return f(*args, **kwargs)

        return wrapper

    def add_to_click(cls, config: CliConfig, namespace, func: T, value, name) -> T:
        """Add function as command to click."""

        types = importlib.import_module(f"{config.client_module}.types")

        value_type = cls.TYPE_MAP.get(value.annotation, click.STRING)
        default_value = value.default if not isinstance(value.default, types.Unset) else None

        is_list = False
        if isinstance(typing.get_origin(value.annotation), list):
            is_list = True

        if isinstance(typing.get_args(value.annotation), tuple):
            for arg in typing.get_args(value.annotation):
                orig = typing.get_origin(arg)
                if isinstance(orig, type) and issubclass(orig, list):
                    is_list = True

        if value.default == inspect.Parameter.empty and not is_list:
            func.__doc__ += f"\n\n{name.upper()}: {value_type} (required)"
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

        namespace[f"{func.__name__}"] = func

        return func

    def iter_api(cls: type["OpenAPICli"], config, namespace, module: str, group: Group) -> None:
        """Iterate over all API classes in a module."""

        module = importlib.import_module(module)
        for sub_module in pkgutil.iter_modules(module.__path__):
            if sub_module.ispkg:
                cls.iter_api(
                    config,
                    namespace,
                    f"{module.__name__}.{sub_module.name}",
                    group.group(
                        sub_module.name,
                        help=f"Actions tagged with `{sub_module.name}` tag",
                    )(lambda: None),
                )
            else:
                full_name = f"{module.__name__}.{sub_module.name}"
                command_name = sub_module.name.replace("_", "-")

                cmd = group.command(command_name)
                func = getattr(importlib.import_module(full_name), "sync_detailed")

                func = cls.final_wrapper(func)

                func.__doc__ = func.__doc__.split("\n")[0] + "\n\n"

                if inspect.signature(func).parameters.get("body"):
                    body_cls = inspect.signature(func).parameters.get("body").annotation
                    func = cls.with_transform_body(func, body_cls)

                if inspect.signature(func).parameters.get("client"):
                    client_cls = inspect.signature(func).parameters.get("client").annotation
                    func = cls.with_client(func, client_cls)

                for name, value in inspect.signature(func).parameters.items():
                    if name == "client":
                        continue

                    if (
                        name == "body"
                        and isinstance(value.annotation, type)
                        and hasattr(value.annotation, "__annotations__")
                    ):
                        for sub_name, sub_value in reversed(
                            inspect.signature(value.annotation).parameters.items()
                        ):
                            func = cls.add_to_click(config, namespace, func, sub_value, sub_name)

                    else:
                        func = cls.add_to_click(config, namespace, func, value, name)

                cmd(click.pass_context(cls.print_result(cls.cls_with_self(func))))

    @staticmethod
    @cli.group("api", help="List of API actions", invoke_without_command=True)
    @click.option("--mode", type=APIMode)
    @click.pass_context
    def api_group(ctx, mode: APIMode = APIMode.default):
        pass

    def setup_commands(cls: type["OpenAPICli"], namespace):
        """Setup click commands for API actions"""

        config = CliConfig.load()

        if config.client_module is not None:
            cls.iter_api(config, namespace, f"{config.client_module}.api", cls.api_group)

    def add_self_to_static_commands(cls, namespace: dict[str, Any]) -> None:
        """Add self to commands in class."""

        for k, v in namespace.items():
            if isinstance(v, Command):
                v.callback = cls.cls_with_self(v.callback)


class OpenAPICli(metaclass=MetaCli):

    @property
    def client_module(self) -> ModuleType:
        if self.config.client_module is None:
            raise click.UsageError("Use `configure` to set the client module")

        return importlib.import_module(self.config.client_module)

    @functools.cached_property
    def config(self) -> CliConfig:
        return CliConfig.load()

    def get_api_client(self, client_cls: type[T] | tuple[type[T]]) -> T:
        """Get an API client instance."""

        if isinstance(client_cls, tuple):
            client_cls = client_cls[0]

        if isinstance(client_cls, type):
            return client_cls(
                base_url=str(self.config.base_url),
                token=str(self.config.token),
            )

    def validate_client_module(self) -> bool:
        """Validate that the client module exists and has all the necessary submodules."""

        required_submodules = ["api", "models", "client", "errors", "types"]

        for submodule in required_submodules:
            if not importlib.import_module(f"{self.client_module.__name__}.{submodule}"):
                raise click.UsageError(
                    f"{self.config.client_module} is missing a {submodule} module"
                )

        return True

    @cli.command("configure")
    @click.option(
        "--client-module",
        help="Client module name. Example: 'fast_api_client'",
    )
    @click.option("--base-url", help="Base API URL")
    @click.option("--token", help="User token")
    def configure(
        self,
        client_module: str | None = None,
        base_url: HttpUrl | None = None,
        token: str = None,
    ) -> None:
        """Configure the Open API CLI to use a specific client module.

        \b
        CLIENT_MODULE: generated module by `openapi-python-client`.
        """

        if self.config.client_module is None and client_module is None:
            raise click.UsageError(
                f"{self.config.client_module} is not set and no client module was provided"
            )

        elif client_module is not None:
            self.config.client_module = client_module
            self.validate_client_module()

        if base_url is not None:
            self.config.base_url = base_url

        if token is not None:
            self.config.token = token

        self.config.save()

        click.echo("Client module configured successfully")


def main():
    return cli()


if __name__ == "__main__":
    cli()
