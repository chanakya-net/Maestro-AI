"""Public CLI for the experimental runtime foundation."""
import argparse
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version

from .resources import RESOURCE_NAMES, resource_path


def package_version() -> str:
    """Report installed distribution metadata without duplicating its version."""
    try:
        return version("ai-skills")
    except PackageNotFoundError:
        return "uninstalled (source checkout)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-skills", description="Experimental AI-Skills Python runtime.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="Inspect the interpreter and maintained resources (read-only).")
    doctor.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        report = doctor_report()
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"AI-Skills {report['version']}")
            print(f"Python {report['python_version']}: {report['interpreter']}")
            print(f"Platform: {report['platform']}")
            print(f"Maintained resources: {len(report['resources'])}")
            for error in report["errors"]:
                print(f"ERROR: {error}", file=sys.stderr)
            print("Doctor: healthy" if report["ok"] else "Doctor: failed")
        return 0 if report["ok"] else 1
    parser.print_help()
    return 0


def doctor_report() -> dict:
    """Validate local interpreter and resource contents without starting tools."""
    errors = []
    present = []
    schema = None
    if sys.version_info < (3, 12):
        errors.append("Python 3.12 or newer is required")
    for name in RESOURCE_NAMES:
        try:
            content = resource_path(name).read_text(encoding="utf-8")
            if not content.strip():
                raise ValueError("empty resource")
            if name == "agent-registry.json":
                registry = json.loads(content)
                schema = registry.get("schema_version")
                if schema != 1 or not isinstance(registry.get("agents"), dict):
                    raise ValueError("invalid registry schema")
            present.append(name)
        except (OSError, ValueError, AttributeError) as exc:
            errors.append(f"{name}: {exc}")
    return {"ok": not errors, "version": package_version(),
            "python_version": platform.python_version(), "interpreter": sys.executable,
            "platform": platform.platform(), "resources": present,
            "registry_schema_version": schema, "errors": errors}
