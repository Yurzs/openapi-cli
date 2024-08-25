import openapi_cli.interfaces.action  # noqa: F401
import openapi_cli.interfaces.client  # noqa: F401
import openapi_cli.interfaces.completions  # noqa: F401
import openapi_cli.interfaces.misc  # noqa: F401
from openapi_cli.interfaces.main import cli


def main():
    return cli()


if __name__ == "__main__":
    cli.group()
