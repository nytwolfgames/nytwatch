from __future__ import annotations

import json
import logging
import platform
import subprocess
import urllib.request
from typing import Optional

from auditor.config import AuditorConfig

log = logging.getLogger(__name__)


def notify(
    config: AuditorConfig,
    title: str,
    message: str,
    pr_url: Optional[str] = None,
) -> None:
    nc = config.notifications

    if nc.desktop:
        _desktop_notify(title, message)

    if nc.slack_webhook:
        _slack_notify(nc.slack_webhook, title, message, pr_url)

    if nc.discord_webhook:
        _discord_notify(nc.discord_webhook, title, message, pr_url)


def _desktop_notify(title: str, message: str) -> None:
    try:
        if platform.system() == "Darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                text=True,
            )
        log.debug("Desktop notification sent")
    except Exception:
        log.warning("Failed to send desktop notification", exc_info=True)


def _slack_notify(webhook: str, title: str, message: str, pr_url: Optional[str]) -> None:
    text = f"*{title}*\n{message}"
    if pr_url:
        text += f"\n<{pr_url}|View PR>"

    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10):
            log.debug("Slack notification sent")
    except Exception:
        log.warning("Failed to send Slack notification", exc_info=True)


def _discord_notify(webhook: str, title: str, message: str, pr_url: Optional[str]) -> None:
    content = f"**{title}**\n{message}"
    if pr_url:
        content += f"\n{pr_url}"

    payload = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10):
            log.debug("Discord notification sent")
    except Exception:
        log.warning("Failed to send Discord notification", exc_info=True)


def format_batch_complete_message(
    batch: dict,
    findings: list[dict],
) -> tuple[str, str]:
    status = batch.get("status", "unknown")
    batch_id = batch.get("id", "unknown")
    finding_count = len(findings)

    title = f"Batch {batch_id}: {status}"

    lines = [
        f"Findings: {finding_count}",
        f"Status: {status}",
    ]

    pr_url = batch.get("pr_url")
    if pr_url:
        lines.append(f"PR: {pr_url}")

    commit_sha = batch.get("commit_sha")
    if commit_sha:
        lines.append(f"Commit: {commit_sha[:8]}")

    test_log = batch.get("test_log")
    if test_log:
        passed = test_log.count("[Passed]")
        failed = test_log.count("[Failed]")
        lines.append(f"Tests: {passed} passed, {failed} failed")

    body = "\n".join(lines)
    return title, body
