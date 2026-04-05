from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from nytwatch.config import AuditorConfig, DEFAULT_CONFIG_PATH, get_active_config_path, get_db_path, init_config, load_config
from nytwatch.database import Database
from nytwatch.web.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nytwatch")


def create_app(config: AuditorConfig, config_path: Optional[Path] = None) -> FastAPI:
    import asyncio
    from nytwatch.ws_manager import manager as ws_manager

    app = FastAPI(title="Nytwatch", version="0.1.0")

    app.state.tracking_active = False

    @app.on_event("startup")
    async def startup():
        ws_manager.set_loop(asyncio.get_event_loop())

    if config.repo_path:
        db = Database(get_db_path(config, config_path))
        db.init_schema()

        # One-time migration: if YAML config has systems and the DB has none, copy them.
        if config.systems and not db.list_systems():
            logger.info(
                "Migrating %d system(s) from YAML config to database", len(config.systems)
            )
            db.replace_systems([
                {
                    "name": s.name,
                    "source_dir": s.source_dir,
                    "paths": list(s.paths),
                    "min_confidence": s.min_confidence,
                    "file_extensions": list(s.file_extensions) if s.file_extensions else None,
                    "claude_fast_mode": s.claude_fast_mode,
                }
                for s in config.systems
            ])

        stale = db.fail_stale_scans()
        if stale:
            logger.warning("Marked %d stale running scan(s) as failed on startup", stale)
    else:
        db = None
        logger.info("No project configured — starting in wizard-only mode (no DB)")

    app.state.db = db
    app.state.config = config
    app.state.config_path = str(config_path) if config_path else ""

    # Start filesystem watcher for the active project
    from nytwatch.tracking.watcher import TrackingWatcher
    watcher = TrackingWatcher(ws_manager, lambda: app.state.db)
    app.state.watcher = watcher
    if config.repo_path:
        watcher.add_watch(config.repo_path)

    static_dir = Path(__file__).parent / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)

    # Set up scheduled scan notifications if a project is configured
    if config.repo_path and config.scan_schedule.incremental_interval_hours > 0:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            def _notify_scan_due(scan_type: str = "incremental") -> None:
                ws_manager.push_scan_due(scan_type, reason="schedule")

            scheduler = BackgroundScheduler()
            scheduler.add_job(
                _notify_scan_due,
                "interval",
                hours=config.scan_schedule.incremental_interval_hours,
                kwargs={"scan_type": "incremental"},
                id="incremental_scan",
                name="Incremental code scan (notify)",
            )

            if config.scan_schedule.rotation_enabled:
                scheduler.add_job(
                    _notify_scan_due,
                    "interval",
                    hours=config.scan_schedule.rotation_interval_hours,
                    kwargs={"scan_type": "rotation"},
                    id="rotation_scan",
                    name="Rotation full scan (notify)",
                )

            scheduler.start()
            app.state.scheduler = scheduler
            logger.info(
                "Scheduled scan notifications every %d hours",
                config.scan_schedule.incremental_interval_hours,
            )
        except Exception:
            logger.exception("Failed to start scheduler")

    @app.on_event("shutdown")
    def shutdown():
        from nytwatch.scan_state import canceller
        if not canceller.is_cancelled:
            canceller.cancel()
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown()
        if hasattr(app.state, "watcher"):
            app.state.watcher.stop()
        active_db = app.state.db
        if active_db is not None:
            stale = active_db.fail_stale_scans()
            if stale:
                logger.warning("Shutdown: marked %d running scan(s) as failed", stale)
            active_db.close()

    return app


def run():
    parser = argparse.ArgumentParser(description="Nytwatch")
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

    # install-plugin command
    install_parser = subparsers.add_parser(
        "install-plugin", help="Install the NytwatchAgent UE5 plugin into a game project"
    )
    install_parser.add_argument("--project", required=True, help="Path to the UE game project root")
    install_parser.add_argument("--force", action="store_true", help="Reinstall even if already up to date")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Run a scan immediately")
    scan_parser.add_argument("--config", default=None, help="Config file path")
    scan_parser.add_argument("--type", default="incremental", choices=["incremental", "full", "rotation"])
    scan_parser.add_argument("--system", default=None, help="System name for full scan")

    args = parser.parse_args()

    if args.command == "install-plugin":
        from nytwatch.tracking.plugin_installer import install_plugin
        sys.exit(install_plugin(args.project, force=args.force))

    if args.command == "init":
        config_path = args.config
        path = init_config(args.repo_path, Path(config_path) if config_path else None)
        print(f"Config created at: {path}")
        print(f"Edit the config to define your game systems and UE paths.")
        return

    if args.command == "scan":
        scan_config_path = Path(args.config) if args.config else None
        config = load_config(scan_config_path)
        db = Database(get_db_path(config, scan_config_path))
        db.init_schema()
        from nytwatch.scanner.scheduler import run_scan
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
        logger.info("Starting Nytwatch on http://%s:%d", host, port)
        uvicorn.run(app, host=host, port=port, log_level="info")
        return

    parser.print_help()


if __name__ == "__main__":
    run()
