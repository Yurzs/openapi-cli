import importlib
import sys
import typing

import attrs
import cattrs.strategies
import click
from plumbum.colors import blue, red  # noqa: F401
from typing_extensions import ForwardRef

from openapi_cli.config import CliConfig
from openapi_cli.symbols import ERROR, INFO
from openapi_cli.utils import echo, get_script_name, patch

ATTRS_TYPE_MAP = {}


config = CliConfig.load()

CONVERTER = cattrs.Converter()
UNION_CONVERTER = cattrs.Converter()


def _gen_forward_ref_hook(t: ForwardRef):
    """Generate a hook for forward reference resolution."""

    t._evaluate(None, ATTRS_TYPE_MAP, set())
    return lambda v, t_: CONVERTER.structure(v, t_.__forward_value__)


CONVERTER.register_structure_hook_factory(
    lambda t: t.__class__ is typing.ForwardRef, _gen_forward_ref_hook
)


def register_models(module):
    """Register models for forward reference resolution."""

    for name, obj in module.__dict__.items():
        if attrs.has(obj) and name not in ATTRS_TYPE_MAP:
            if obj.__module__ != module.__name__:
                register_models(importlib.import_module(obj.__module__))
            else:
                attrs.resolve_types(obj)
                ATTRS_TYPE_MAP[obj.__name__] = obj


if config.client_module_name and len(sys.argv) > 1 and sys.argv[1] == "action":
    try:
        with patch(typing, "TYPE_CHECKING", True):
            types = importlib.import_module(f"{config.client_module_name}.types")

            # replace Unset with None
            types.Unset = type(None)
            types.UNSET = None

            models = importlib.import_module(f"{config.client_module_name}.models")
            api = importlib.import_module(f"{config.client_module_name}.api")

        register_models(types)
        register_models(models)
        register_models(api)
    except ModuleNotFoundError:
        ctx = click.get_current_context(True)
        script_name = sys.argv[0].split("/")[-1] if ctx is None else get_script_name(ctx)

        echo("Client module not found" | red, ERROR)
        echo(f"Use `{script_name} client` to set up the client module" | blue, INFO)
        sys.exit(1)
