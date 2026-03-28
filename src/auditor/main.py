from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from auditor.config import AuditorConfig, get_db_path, init_config, load_config
from auditor.database import Database
from auditor.web.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("auditor")


def create_app(config: AuditorConfig) -> FastAPI:
    app = FastAPI(title="Code Auditor", version="0.1.0")

    db = Database(get_db_path(config))
    db.init_schema()

    app.state.db = db
    app.state.config = config

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
        config_path = getattr(args, "config", None)
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8420)

        try:
            config = load_config(Path(config_path) if config_path else None)
        except FileNotFoundError as e:
            print(str(e))
            sys.exit(1)

        app = create_app(config)
        logger.info("Starting Code Auditor on http://%s:%d", host, port)
        uvicorn.run(app, host=host, port=port, log_level="info")
        return

    parser.print_help()


if __name__ == "__main__":
    run()
