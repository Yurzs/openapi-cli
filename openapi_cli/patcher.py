import importlib
import pkgutil
from contextlib import contextmanager

from plumbum.cmd import mkdir, mv, touch

from openapi_cli.separator import CLI_SEPARATOR


def create_new_submodule(
    module_name: str,
    module,
    sub_module_name: str,
    sub_module,
    separator: str = CLI_SEPARATOR,
) -> str:
    """Create a new submodule by renaming it to remove the module name prefix."""

    from openapi_cli.cli import MOVE, echo

    new_sub_module_name, new_file_name = sub_module_name.split(separator)

    old_path = f"{module.__path__[0]}/{sub_module_name}"

    full_name = f"{module_name}.{new_sub_module_name}"
    new_path = f"{module.__path__[0]}/{new_sub_module_name}"

    new_sub_file_path = sub_module.__file__.replace(
        f"{old_path}.py",
        f"{new_path}/{new_file_name}.py",
    )

    echo(f"Patching {sub_module_name} to {full_name}", MOVE)

    mkdir["-p", new_path]()
    touch[f"{new_path}/__init__.py"]()

    mv[
        sub_module.__file__,
        new_sub_file_path,
    ]()

    return full_name


def patch_submodule(module_name: str, separator: str = CLI_SEPARATOR):
    """Patch a submodule by renaming it to remove the module name prefix."""

    from openapi_cli.cli import MOVE, echo

    module = importlib.import_module(module_name)
    module_last_name = f"{module_name.split(".")[-1]}_"

    new_submodules = set()

    for sub_module in pkgutil.iter_modules(module.__path__):
        inner_module = importlib.import_module(f"{module_name}.{sub_module.name}")
        if sub_module.ispkg:
            patch_submodule(f"{module_name}.{sub_module.name}")

        elif inner_module.__name__ != "__init__":
            new_sub_module_name = sub_module.name

            if sub_module.name.startswith(module_last_name):
                new_sub_module_name = sub_module.name[len(module_last_name) :]

            new_sub_file_path = inner_module.__file__.replace(
                f"{sub_module.name}.py",
                f"{new_sub_module_name}.py",
            )

            echo(f"Moving {sub_module.name} to {module.__name__}.{new_sub_module_name}", MOVE)

            mv[
                inner_module.__file__,
                new_sub_file_path,
            ]()

            if separator in new_sub_module_name:
                new_submodules.add(
                    create_new_submodule(
                        module_name,
                        module,
                        new_sub_module_name,
                        importlib.import_module(f"{module_name}.{new_sub_module_name}"),
                        separator=separator,
                    )
                )

    for new_submodule in new_submodules:
        patch_submodule(new_submodule)


@contextmanager
def patch(object_, attribute_name, value):
    """Patch an object attribute with a new value."""

    old_value = getattr(object_, attribute_name)
    try:
        setattr(object_, attribute_name, value)
        yield
    finally:
        setattr(object_, attribute_name, old_value)
