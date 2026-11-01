"""Public CLI and runtime selection before version-specific parsing."""
import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from .resources import RESOURCE_NAMES, resource_path


def package_version() -> str:
    """Report installed distribution metadata without duplicating its version."""
    try:
        return version("ai-skills")
    except PackageNotFoundError:
        return "uninstalled (source checkout)"


def runtime_command(arguments: list[str]) -> list[str] | None:
    """Select the run's immutable interpreter before importing its commands."""
    if not arguments or any(arg in ('--help', '-h', '--version') for arg in arguments):
        return None
    if arguments[0] in ('doctor', 'install', 'uninstall'):
        return None

    def option(name):
        for index, arg in enumerate(arguments):
            if arg.startswith(name + '='):
                return arg.split('=', 1)[1]
            if arg == name and index + 1 < len(arguments):
                return arguments[index + 1]
        return None

    config = {}
    if source := option('--config'):
        config = json.loads(Path(source).read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('command configuration must be an object')
    candidate = (option('--run-dir') or option('--run-root') or config.get('run_root')
                 or os.environ.get('AI_SKILLS_RUN_ROOT'))
    state_file = option('--state-file') or config.get('state_file')
    if not candidate and state_file:
        parent = Path(state_file).absolute().parent
        candidate = parent.parent if parent.name == '.run-with-it' else parent
    if not candidate:
        agent_run = config.get('agent_run', {})
        if not isinstance(agent_run, dict):
            raise ValueError('agent_run must be an object')
        candidate = option('--repo-root') or agent_run.get('repo_root') or Path.cwd()
    target = Path(os.path.abspath(candidate))
    from .installation import pair_runtime_pin, resolve_runtime
    pair_pin = arguments[0] == 'pair' and pair_runtime_pin(target) is not None
    if not pair_pin:
        for directory in (target, *target.parents):
            if (directory / '.run-with-it').is_dir():
                target = directory
                break
    engine = target / '.run-with-it/engine.json'
    legacy = target / '.run-with-it/main-state.json'
    launching = (arguments[:2] in (['run', 'pool'], ['worker', 'dispatch'])
                 or arguments[0] == 'agent' and (len(arguments) == 1 or arguments[1] not in ('list', 'models')))
    if launching and not engine.exists() and legacy.exists():
        state = json.loads(legacy.read_text(encoding='utf-8'))
        if not isinstance(state, dict):
            raise ValueError('legacy state must be an object')
        active = state.get('active_pool_issues') or any(
            item.get('status') in ('in_progress', 'running', 'merging')
            for item in state.get('issue_registry', {}).values() if isinstance(item, dict))
        if active:
            raise ValueError('legacy run must be quiescent before switching engines')
    selected = resolve_runtime(target)
    if os.path.normcase(os.path.abspath(selected)) == os.path.normcase(os.path.abspath(sys.executable)):
        return None
    # A source checkout or caller's PYTHONPATH must not override the pin.
    return [str(selected), '-I', '-m', 'ai_skills', *arguments]


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        # Actual CLI streams carry UTF-8 tasks and machine-readable output on
        # every platform. Explicit-argv library calls retain caller ownership.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if reconfigure := getattr(stream, 'reconfigure', None):
                reconfigure(encoding='utf-8')
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if command := runtime_command(arguments):
            return subprocess.run(command, check=False).returncode
    except (OSError, ValueError) as error:
        print(f'ai-skills runtime selection: {error}', file=sys.stderr)
        return 2
    modules = {'agent': 'runner', 'worker': 'dispatch', 'pair': 'pair',
               'state': 'state', 'route': 'routing', 'artifact': 'artifacts',
               'github': 'github', 'report': 'reports', 'git': 'git_operations',
               'install': 'installation', 'uninstall': 'installation'}
    if arguments and arguments[0] == 'run':
        operation = arguments[1] if len(arguments) > 1 else '--help'
        if operation in ('watch', 'stop', 'inspect'):
            return importlib.import_module('ai_skills.dispatch').main([operation, *arguments[2:]])
        return importlib.import_module('ai_skills.pool').main(arguments[2:] if operation == 'pool' else arguments[1:])
    if arguments and arguments[0] in modules:
        command, *rest = arguments
        if command == 'report' and rest and rest[0] == 'pr':
            rest = ['render', *rest[1:]]
        if command == 'uninstall':
            rest = ['uninstall', *rest]
        return importlib.import_module('ai_skills.' + modules[command]).main(rest)
    parser = argparse.ArgumentParser(prog="ai-skills", description="Native cross-platform AI-Skills orchestration runtime.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="Inspect the interpreter and maintained resources (read-only).")
    doctor.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    for name in [*modules, 'run']:
        commands.add_parser(name, add_help=False, help='Native ' + name + ' commands.')
    args = parser.parse_args(arguments)
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
                from ai_skills.registry import load_registry
                load_registry()
            present.append(name)
        except (OSError, ValueError, AttributeError) as exc:
            errors.append(f"{name}: {exc}")
    return {"ok": not errors, "version": package_version(),
            "python_version": platform.python_version(), "interpreter": sys.executable,
            "platform": platform.platform(), "resources": present,
            "registry_schema_version": schema, "errors": errors}
