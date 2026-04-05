from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class SystemDef(BaseModel):
    name: str
    paths: list[str]
    source_dir: str = ""  # parent active source directory this system lives under
    # Per-system overrides — None means "inherit global setting"
    min_confidence: Optional[str] = None
    file_extensions: Optional[list[str]] = None
    claude_fast_mode: Optional[bool] = None


class ScanSchedule(BaseModel):
    incremental_interval_hours: int = 4
    rotation_enabled: bool = False
    rotation_interval_hours: int = 24


class BuildConfig(BaseModel):
    ue_installation_dir: str = ""
    ue_editor_cmd: str = ""  # derived from ue_installation_dir if blank
    project_file: str = ""
    build_timeout_seconds: int = 1800
    test_timeout_seconds: int = 600


class NotificationConfig(BaseModel):
    desktop: bool = True
    slack_webhook: Optional[str] = None
    discord_webhook: Optional[str] = None


class AuditorConfig(BaseModel):
    project_name: str = ""
    repo_path: str = ""
    systems: list[SystemDef] = Field(default_factory=list)
    scan_schedule: ScanSchedule = Field(default_factory=ScanSchedule)
    build: BuildConfig = Field(default_factory=BuildConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    data_dir: str = "~/.nytwatch"
    claude_fast_mode: bool = True
    min_confidence: str = "medium"
    file_extensions: list[str] = Field(default_factory=lambda: [".h", ".cpp"])
    # The git branch the auditor operates on.  Empty string means auto-detect
    # from the repo's origin/HEAD at runtime (see git_ops.get_default_branch).
    git_branch: str = ""


DEFAULT_CONFIG_PATH = Path("~/.nytwatch/config.yaml").expanduser()
ACTIVE_POINTER_PATH = Path("~/.nytwatch/.active").expanduser()


def get_active_config_path() -> Optional[Path]:
    """Return the currently active project config path, or None if none is set."""
    if ACTIVE_POINTER_PATH.exists():
        try:
            p = Path(ACTIVE_POINTER_PATH.read_text().strip())
            if p.exists():
                return p
        except Exception:
            pass
    return None


def set_active_config_path(path: Path) -> None:
    """Record the given config path as the active project."""
    ACTIVE_POINTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_POINTER_PATH.write_text(str(path))


def load_config(path: Optional[Path] = None) -> AuditorConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Run 'nytwatch init' or create it manually.\n"
            f"See README.md for config format."
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return AuditorConfig(**(raw or {}))


def save_full_config(config: AuditorConfig, path: Optional[Path] = None) -> None:
    """Save all config fields (used by wizard and repair).

    Systems are stored in the database, not the YAML, so they are intentionally
    omitted here.
    """
    config_path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict = {
        "project_name": config.project_name,
        "repo_path": config.repo_path,
        "scan_schedule": {
            "incremental_interval_hours": config.scan_schedule.incremental_interval_hours,
            "rotation_enabled": config.scan_schedule.rotation_enabled,
            "rotation_interval_hours": config.scan_schedule.rotation_interval_hours,
        },
        "build": {
            "ue_installation_dir": config.build.ue_installation_dir,
            "ue_editor_cmd": config.build.ue_editor_cmd,
            "project_file": config.build.project_file,
            "build_timeout_seconds": config.build.build_timeout_seconds,
            "test_timeout_seconds": config.build.test_timeout_seconds,
        },
        "notifications": {
            "desktop": config.notifications.desktop,
        },
        "data_dir": config.data_dir,
        "claude_fast_mode": config.claude_fast_mode,
        "min_confidence": config.min_confidence,
        "file_extensions": list(config.file_extensions),
        "git_branch": config.git_branch,
    }
    if config.notifications.slack_webhook:
        raw["notifications"]["slack_webhook"] = config.notifications.slack_webhook
    if config.notifications.discord_webhook:
        raw["notifications"]["discord_webhook"] = config.notifications.discord_webhook
    with open(config_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def list_project_configs() -> list[dict]:
    """Scan ~/.nytwatch/ for *.yaml project config files."""
    search_dir = DEFAULT_CONFIG_PATH.parent
    results = []
    if not search_dir.exists():
        return results
    for yaml_path in sorted(search_dir.glob("*.yaml")):
        try:
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}
            repo = raw.get("repo_path", "")
            # Skip blank/unconfigured yamls (e.g. legacy empty config.yaml)
            if not repo:
                continue
            project_name = raw.get("project_name", "").strip()
            display_name = project_name or (Path(repo).name if repo else yaml_path.stem)
            results.append({
                "path": str(yaml_path).replace("\\", "/"),
                "repo_path": repo,
                "name": display_name,
            })
        except Exception:
            pass
    return results


def validate_config_errors(config: AuditorConfig, systems: Optional[list] = None) -> list[str]:
    """Return a list of human-readable validation problems.

    Pass the ``systems`` list (from the database) to include system-level checks.
    Each entry must have ``name`` and ``paths`` keys.
    """
    errors = []
    repo = Path(config.repo_path).expanduser()
    if not repo.exists():
        errors.append(f"repo_path does not exist: {config.repo_path}")
    elif not (repo / ".git").exists():
        errors.append(f"repo_path is not a git repository: {config.repo_path}")

    if systems is not None:
        if not systems:
            errors.append("No systems configured — add at least one system with paths")

        seen_paths: dict[str, str] = {}
        for sys in systems:
            name = sys.get("name", "").strip() if isinstance(sys, dict) else getattr(sys, "name", "")
            paths = sys.get("paths", []) if isinstance(sys, dict) else list(getattr(sys, "paths", []))
            if not name:
                errors.append("A system has an empty name")
            if not paths:
                errors.append(f"System '{name}' has no paths")
            for p in paths:
                norm = p.replace("\\", "/").rstrip("/") + "/"
                if norm in seen_paths:
                    errors.append(
                        f"Path '{p}' is shared by '{seen_paths[norm]}' and '{name}'"
                    )
                else:
                    seen_paths[norm] = name
                if repo.exists() and not (repo / p).exists():
                    errors.append(f"System '{name}': path not found in repo: {p}")
    return errors


def detect_systems_from_repo(repo_path: str) -> list[dict]:
    """Auto-detect systems from repo structure using UE heuristics.

    Looks for:
    - *.uplugin files  → Plugin systems (Source/ subdirectory)
    - Source/**/*.Build.cs → Game module systems
    """
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists():
        return []

    _skip = {
        "Binaries", "Intermediate", "Saved", "DerivedDataCache",
        "Content", "__pycache__", "node_modules", ".git",
    }

    def _in_skip(p: Path) -> bool:
        return any(part in _skip for part in p.relative_to(repo).parts)

    candidates: list[dict] = []
    seen_paths: set[str] = set()

    # *.uplugin → Plugin system
    for uplugin in sorted(repo.rglob("*.uplugin")):
        try:
            if _in_skip(uplugin):
                continue
            source = uplugin.parent / "Source"
            base = source if source.exists() else uplugin.parent
            rel = str(base.relative_to(repo)).replace("\\", "/").rstrip("/") + "/"
            if rel not in seen_paths:
                seen_paths.add(rel)
                candidates.append({"name": uplugin.stem, "paths": [rel], "hint": "plugin"})
        except ValueError:
            pass

    # Source/**/*.Build.cs → game module systems
    source_root = repo / "Source"
    if source_root.exists():
        for build_cs in sorted(source_root.rglob("*.Build.cs")):
            try:
                if _in_skip(build_cs):
                    continue
                mod_dir = build_cs.parent
                rel = str(mod_dir.relative_to(repo)).replace("\\", "/").rstrip("/") + "/"
                if rel not in seen_paths:
                    seen_paths.add(rel)
                    name = build_cs.stem.replace(".Build", "")
                    candidates.append({"name": name, "paths": [rel], "hint": "module"})
            except ValueError:
                pass

    return candidates


def get_data_dir(config: AuditorConfig) -> Path:
    data_dir = Path(config.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path(config: AuditorConfig, config_path: Optional[Path] = None) -> Path:
    """Return the DB path for this project.

    When config_path is provided the DB is named after the config file stem
    (e.g. ~/.nytwatch/greenleaf.yaml → ~/.nytwatch/greenleaf.db).
    On first use the legacy nytwatch.db is renamed automatically so existing
    users don't lose their data.
    Falls back to nytwatch.db when no config_path is known.
    """
    import logging
    _log = logging.getLogger(__name__)
    data_dir = get_data_dir(config)
    if config_path:
        stem = Path(config_path).expanduser().stem
        db_path = data_dir / f"{stem}.db"
        legacy = data_dir / "nytwatch.db"
        if not db_path.exists() and legacy.exists():
            legacy.rename(db_path)
            _log.info("Migrated legacy nytwatch.db → %s", db_path.name)
        return db_path
    return data_dir / "nytwatch.db"


def init_config(repo_path: str, config_path: Optional[Path] = None) -> Path:
    config_path = config_path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    default = {
        "repo_path": repo_path,
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
        "data_dir": "~/.nytwatch",
        "claude_fast_mode": True,
        "min_confidence": "medium",
        "file_extensions": [".h", ".cpp"],
    }

    with open(config_path, "w") as f:
        yaml.dump(default, f, default_flow_style=False, sort_keys=False)

    return config_path
