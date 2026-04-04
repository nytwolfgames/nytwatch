from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from auditor.config import AuditorConfig, DEFAULT_CONFIG_PATH, get_active_config_path, get_db_path, init_config, load_config
from auditor.database import Database
from auditor.web.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("auditor")


def create_app(config: AuditorConfig, config_path: Optional[Path] = None) -> FastAPI:
    import asyncio
    from auditor.ws_manager import manager as ws_manager

    app = FastAPI(title="Code Auditor", version="0.1.0")

    @app.on_event("startup")
    async def startup():
        import threading
        ws_manager.set_loop(asyncio.get_event_loop())

        # Auto-trigger incremental scan if new commits arrived since last scan
        if not config.repo_path:
            return  # No project configured yet
        try:
            from auditor.scanner.incremental import get_current_commit
            current_commit = get_current_commit(config.repo_path)
            last_commit = db.get_config("last_scan_commit", "")
            if current_commit != last_commit:
                logger.info(
                    "New commits detected since last scan (%s → %s) — triggering incremental scan",
                    last_commit or "none", current_commit[:8],
                )
                from auditor.scan_state import canceller
                canceller.reset()

                def _auto_scan():
                    from auditor.database import Database
                    from auditor.scanner.scheduler import run_scan
                    thread_db = Database(get_db_path(config))
                    try:
                        run_scan(config, thread_db, scan_type="incremental")
                    except Exception:
                        logger.exception("Auto incremental scan failed")
                    finally:
                        thread_db.close()

                threading.Thread(target=_auto_scan, daemon=True, name="auto-incremental").start()
            else:
                logger.info("No new commits since last scan (%s) — skipping auto scan", last_commit[:8] if last_commit else "none")
        except Exception:
            logger.exception("Failed to check for new commits on startup")

    db = Database(get_db_path(config))
    db.init_schema()

    # One-time migration: if YAML config has systems and the DB has none, copy them.
    if config.systems and not db.list_systems():
        logger.info(
            "Migrating %d system(s) from YAML config to database", len(config.systems)
        )
        db.replace_systems([
            {
                "name": s.name,
                "source_dir": s.source_dir,  # "" for legacy YAML systems
                "paths": list(s.paths),
                "min_confidence": s.min_confidence,
                "file_extensions": list(s.file_extensions) if s.file_extensions else None,
                "claude_fast_mode": s.claude_fast_mode,
            }
            for s in config.systems
        ])

    app.state.db = db
    app.state.config = config
    app.state.config_path = str(config_path) if config_path else ""

    stale = db.fail_stale_scans()
    if stale:
        logger.warning("Marked %d stale running scan(s) as failed on startup", stale)

    static_dir = Path(__file__).parent / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)

    # Set up scheduled scans if configured
    if config.scan_schedule.incremental_interval_hours > 0:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from auditor.scanner.scheduler import run_scan

            scheduler = BackgroundScheduler()
            scheduler.add_job(
                run_scan,
                "interval",
                hours=config.scan_schedule.incremental_interval_hours,
                args=[config, db, "incremental"],
                id="incremental_scan",
                name="Incremental code scan",
            )

            if config.scan_schedule.rotation_enabled:
                scheduler.add_job(
                    run_scan,
                    "interval",
                    hours=config.scan_schedule.rotation_interval_hours,
                    args=[config, db, "rotation"],
                    id="rotation_scan",
                    name="Rotation full scan",
                )

            scheduler.start()
            app.state.scheduler = scheduler
            logger.info(
                "Scheduled incremental scans every %d hours",
                config.scan_schedule.incremental_interval_hours,
            )
        except Exception:
            logger.exception("Failed to start scheduler")

    @app.on_event("shutdown")
    def shutdown():
        from auditor.scan_state import canceller
        if not canceller.is_cancelled:
            # Kill any active Claude subprocess and signal scan threads to stop
            canceller.cancel()
        stale = db.fail_stale_scans()
        if stale:
            logger.warning("Shutdown: marked %d running scan(s) as failed", stale)
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown()
        db.close()

    return app


def run():
    parser = argparse.ArgumentParser(description="Code Auditor Agent")
    subparsers = parser.add_subparsers(dest="command")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize config file")
    init_parser.add_argument("repo_path", help="Path to the game repository")
    init_parser.add_argument("--config", default=None, help="Config file path")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start the dashboard server")
    serve_parser.add_argument("--config", default=None, help="Config file path")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=8420, help="Port to bind to")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Run a scan immediately")
    scan_parser.add_argument("--config", default=None, help="Config file path")
    scan_parser.add_argument("--type", default="incremental", choices=["incremental", "full", "rotation"])
    scan_parser.add_argument("--system", default=None, help="System name for full scan")

    args = parser.parse_args()

    if args.command == "init":
        config_path = args.config
        path = init_config(args.repo_path, Path(config_path) if config_path else None)
        print(f"Config created at: {path}")
        print(f"Edit the config to define your game systems and UE paths.")
        return

    if args.command == "scan":
        config = load_config(Path(args.config) if args.config else None)
        db = Database(get_db_path(config))
        db.init_schema()
        from auditor.scanner.scheduler import run_scan
        scan_id = run_scan(config, db, scan_type=args.type, system_name=args.system)
        print(f"Scan complete: {scan_id}")
        db.close()
        return

    if args.command == "serve" or args.command is None:
        config_path_str = getattr(args, "config", None)
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8420)

        if config_path_str:
            # Explicit --config flag takes precedence
            resolved_config_path = Path(config_path_str).expanduser()
        else:
            # Use the active project pointer; fall back to legacy config.yaml only if it exists
            resolved_config_path = get_active_config_path()
            if resolved_config_path is None and DEFAULT_CONFIG_PATH.exists():
                resolved_config_path = DEFAULT_CONFIG_PATH

        if resolved_config_path is not None:
            try:
                config = load_config(resolved_config_path)
            except FileNotFoundError:
                config = AuditorConfig()
                resolved_config_path = None
        else:
            # No project configured yet — start blank so the wizard can run
            config = AuditorConfig()

        app = create_app(config, config_path=resolved_config_path)
        logger.info("Starting Code Auditor on http://%s:%d", host, port)
        uvicorn.run(app, host=host, port=port, log_level="info")
        return

    parser.print_help()


if __name__ == "__main__":
    run()
