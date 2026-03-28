from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class SystemDef(BaseModel):
    name: str
    paths: list[str]


class ScanSchedule(BaseModel):
    incremental_interval_hours: int = 4
    rotation_enabled: bool = False
    rotation_interval_hours: int = 24


class BuildConfig(BaseModel):
    ue_editor_cmd: str = ""
    project_file: str = ""
    build_timeout_seconds: int = 1800
    test_timeout_seconds: int = 600


class NotificationConfig(BaseModel):
    desktop: bool = True
    slack_webhook: Optional[str] = None
    discord_webhook: Optional[str] = None


class AuditorConfig(BaseModel):
    repo_path: str
    systems: list[SystemDef] = Field(default_factory=list)
    scan_schedule: ScanSchedule = Field(default_factory=ScanSchedule)
    build: BuildConfig = Field(default_factory=BuildConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    data_dir: str = "~/.code-auditor"
    claude_fast_mode: bool = True
    min_confidence: str = "medium"
    file_extensions: list[str] = Field(default_factory=lambda: [".h", ".cpp"])


DEFAULT_CONFIG_PATH = Path("~/.code-auditor/config.yaml").expanduser()


def load_config(path: Optional[Path] = None) -> AuditorConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Run 'code-auditor init' or create it manually.\n"
            f"See README.md for config format."
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return AuditorConfig(**raw)


def get_data_dir(config: AuditorConfig) -> Path:
    data_dir = Path(config.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path(config: AuditorConfig) -> Path:
    return get_data_dir(config) / "auditor.db"


def init_config(repo_path: str, config_path: Optional[Path] = None) -> Path:
    config_path = config_path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    default = {
        "repo_path": repo_path,
        "systems": [
            {
                "name": "Example",
                "paths": ["Source/MyGame/Example/"],
            }
        ],
        "scan_schedule": {
            "incremental_interval_hours": 4,
            "rotation_enabled": False,
            "rotation_interval_hours": 24,
        },
        "build": {
            "ue_editor_cmd": "/path/to/UnrealEditor-Cmd",
            "project_file": "/path/to/MyGame.uproject",
            "build_timeout_seconds": 1800,
            "test_timeout_seconds": 600,
        },
        "notifications": {
            "desktop": True,
        },
        "data_dir": "~/.code-auditor",
        "claude_fast_mode": True,
        "min_confidence": "medium",
        "file_extensions": [".h", ".cpp"],
    }

    with open(config_path, "w") as f:
        yaml.dump(default, f, default_flow_style=False, sort_keys=False)

    return config_path
