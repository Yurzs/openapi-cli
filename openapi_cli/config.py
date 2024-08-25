from functools import cached_property
from json import JSONEncoder
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, HttpUrl

CONFIG_FOLDER = Path(".openapi_cli").absolute()
CONFIG_FILE = CONFIG_FOLDER.joinpath("config.json")


class CliConfig(BaseModel):
    """CLI configuration file model."""

    client_module_name: str | None = Field(None, description="Python module containing the client")
    base_url: HttpUrl | None = Field(None, description="Base URL of the API")
    token: str | None = Field(None, description="API token")
    editor: str | None = Field(None, description="Text editor to use for editing JSON")

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
            )
        )

    @cached_property
    def json_encoder(self) -> type[JSONEncoder]:
        return JSONEncoder
