from __future__ import annotations

import logging
import platform
import subprocess

from nytwatch.config import AuditorConfig

log = logging.getLogger(__name__)


def _current_platform() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Mac"
    if system == "Linux":
        return "Linux"
    return "Win64"


def run_ue_build(config: AuditorConfig) -> tuple[bool, str]:
    cmd = [
        config.build.ue_editor_cmd,
        config.build.project_file,
        "-build",
        f"-platform={_current_platform()}",
        "-configuration=Development",
    ]

    log.info("Running UE build: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.build.build_timeout_seconds,
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0

        if success:
            log.info("Build succeeded")
        else:
            log.error("Build failed (exit %d)", result.returncode)

        return success, output

    except subprocess.TimeoutExpired:
        msg = f"Build timed out after {config.build.build_timeout_seconds}s"
        log.error(msg)
        return False, msg
