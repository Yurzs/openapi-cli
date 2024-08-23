import importlib
import pkgutil

import click
from plumbum.cmd import mv, mkdir, touch

CLI_SEPARATOR = "_clisubmodule_"


def create_new_submodule(
    module_name: str,
    module,
    sub_module_name: str,
    sub_module,
) -> str:
    """Create a new submodule by renaming it to remove the module name prefix."""

    new_sub_module_name, new_file_name = sub_module_name.split(
        CLI_SEPARATOR,
    )

    old_path = f"{module.__path__[0]}/{sub_module_name}"

    full_name = f"{module_name}.{new_sub_module_name}"
    new_path = f"{module.__path__[0]}/{new_sub_module_name}"

    new_sub_file_path = sub_module.__file__.replace(
        f"{old_path}.py",
        f"{new_path}/{new_file_name}.py",
    )

    print(f"Patching {sub_module_name} to {full_name}")

    mkdir["-p", new_path]()
    touch[f"{new_path}/__init__.py"]()

    mv[
        sub_module.__file__,
        new_sub_file_path,
    ]()

    return full_name


def patch_submodule(module_name: str):
    """Patch a submodule by renaming it to remove the module name prefix."""

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

            print(f"Patching {sub_module.name} to {module.__name__}.{new_sub_module_name}")

            mv[
                inner_module.__file__,
                new_sub_file_path,
            ]()

            if CLI_SEPARATOR in new_sub_module_name:
                new_submodules.add(
                    create_new_submodule(
                        module_name,
                        module,
                        new_sub_module_name,
                        importlib.import_module(f"{module_name}.{new_sub_module_name}"),
                    )
                )

    for new_submodule in new_submodules:
        patch_submodule(new_submodule)
