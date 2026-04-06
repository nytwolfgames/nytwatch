from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _find_plugin_source() -> Path:
    """Locate the bundled NytwatchAgent plugin source."""
    import nytwatch

    # Installed wheel layout: share/nytwatch/ue5-plugin/NytwatchAgent
    pkg_root = Path(nytwatch.__file__).parent
    wheel_path = pkg_root.parent.parent / "share" / "nytwatch" / "ue5-plugin" / "NytwatchAgent"
    if wheel_path.exists():
        return wheel_path

    # Dev/editable install: repo root / ue5-plugin / NytwatchAgent
    repo_path = Path(__file__).parent.parent.parent.parent / "ue5-plugin" / "NytwatchAgent"
    if repo_path.exists():
        return repo_path

    raise FileNotFoundError(
        f"Plugin source not found. Tried:\n  {wheel_path}\n  {repo_path}"
    )


def _patch_uproject(uproject_path: Path) -> None:
    """
    Ensure the .uproject file has the NytwatchAgent plugin entry enabled.
    Writes atomically via a .tmp file.
    """
    text = uproject_path.read_text(encoding="utf-8")
    data = json.loads(text)

    plugins: list[dict] = data.setdefault("Plugins", [])

    # Check if entry already exists
    for entry in plugins:
        if entry.get("Name") == "NytwatchAgent":
            if not entry.get("Enabled", False):
                entry["Enabled"] = True
                print("   Enabled existing NytwatchAgent entry in .uproject.")
            else:
                print("   NytwatchAgent already enabled in .uproject.")
            break
    else:
        plugins.append({"Name": "NytwatchAgent", "Enabled": True})
        print("   Added NytwatchAgent to .uproject Plugins list.")

    # Atomic write
    tmp_path = uproject_path.with_suffix(".uproject.tmp")
    tmp_path.write_text(
        json.dumps(data, indent="\t") + "\n", encoding="utf-8"
    )
    tmp_path.replace(uproject_path)


def install_plugin(project_path: str, force: bool = False) -> int:
    """
    Install the NytwatchAgent UE5 plugin into a game project.
    Returns 0 on success, 1 on failure.
    """
    target = Path(project_path).expanduser().resolve()

    uproject_files = list(target.glob("*.uproject"))
    if not uproject_files:
        print(
            f"Error: No .uproject file found in {target}\n"
            "Make sure the path points to an Unreal Engine project root.",
            file=sys.stderr,
        )
        return 1

    uproject_file = uproject_files[0]
    project_name = uproject_file.stem
    print(f"Found project: {project_name}")

    try:
        plugin_src = _find_plugin_source()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    bundled_version = (plugin_src / "VERSION").read_text().strip()

    dest = target / "Plugins" / "NytwatchAgent"
    existing_version_file = dest / "VERSION"

    if existing_version_file.exists():
        existing_version = existing_version_file.read_text().strip()
        if existing_version == bundled_version and not force:
            print(
                f"Already installed at current version ({bundled_version}). "
                "Use --force to reinstall."
            )
            _patch_uproject(uproject_file)
            return 0
        elif existing_version != bundled_version:
            print(f"Upgrading from {existing_version} to {bundled_version}.")
        else:
            print(f"Reinstalling ({bundled_version}) with --force.")

    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(plugin_src), str(dest), dirs_exist_ok=True)

    import nytwatch
    manifest = {
        "source_version": bundled_version,
        "installed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nytwatch_version": getattr(nytwatch, "__version__", "0.1.0"),
    }
    (dest / ".nytwatch_install").write_text(json.dumps(manifest, indent=2))

    _patch_uproject(uproject_file)

    print(f"\nNytwatchAgent plugin installed successfully.")
    print(f"\nLocation : {dest}/")
    print(f"Version  : {bundled_version}")
    print(f"\nNext steps:")
    print(f"  1. Open your project in the Unreal Editor")
    print(f"  2. Recompile the project when prompted")
    print(f"  3. Start the Nytwatch server and arm systems from Settings")
    return 0
