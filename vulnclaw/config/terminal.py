"""Complete, independent terminal palettes stored in the main configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

DEFAULT_TERMINAL = json.loads(Path(__file__).with_name("terminal_themes.json").read_text())
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
TerminalTheme = create_model(
    "TerminalTheme",
    __config__=ConfigDict(extra="forbid"),
    **{key: (HexColor, ...) for key in DEFAULT_TERMINAL["themes"]["blue"]},
)


class TerminalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = "blue"
    themes: dict[str, TerminalTheme] = Field(
        default_factory=lambda: {
            name: TerminalTheme.model_validate(colors)
            for name, colors in DEFAULT_TERMINAL["themes"].items()
        },
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_themes(self) -> TerminalConfig:
        for name in self.themes:
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
                raise ValueError("Theme names must start with a lowercase letter and use letters, digits, - or _")
        if self.theme not in self.themes:
            raise ValueError(f"Unknown terminal theme: {self.theme}")
        return self
