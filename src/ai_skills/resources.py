"""Read maintained prompts and registry without copying executable legacy assets."""
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

RESOURCE_NAMES = (
    "agent-registry.json",
    "artifact-recovery-prompt.md",
    "complexity-prompt.md",
    "coordinator-rules.md",
    "main-orchestrator-rules.md",
    "merge-recovery-prompt.md",
    "modifier-prompt.md",
    "pair-colleague-agent-prompt.md",
    "plan-prompt.md",
    "prompt.md",
    "review-prompt.md",
    "sub-coordinator-prompt.md",
)


def resource_root() -> Traversable:
    """Locate packaged resources, or maintained assets in a source checkout."""
    packaged = files("ai_skills").joinpath("data")
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "assets").is_dir():
        return checkout / "assets"
    raise FileNotFoundError("AI-Skills maintained package resources are missing")


def resource_path(name: str) -> Traversable:
    """Resolve an allowlisted prompt/registry; never expose arbitrary paths."""
    if name not in RESOURCE_NAMES:
        raise ValueError(f"Unknown maintained resource: {name!r}")
    path = resource_root().joinpath(name)
    if not path.is_file():
        raise FileNotFoundError(f"Missing maintained resource: {name}")
    return path


SKILL_NAMES = (
    'break-req', 'create-git-issue', 'help-me-debug', 'pair-colleague',
    'run-with-it', 'save-tokens', 'tdd-implementation',
)


def skill_root() -> Traversable:
    """Versioned skill definitions shipped alongside this engine's prompts."""
    packaged = files('ai_skills').joinpath('skills')
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / 'pyproject.toml').is_file() and (checkout / 'skills').is_dir():
        return checkout / 'skills'
    raise FileNotFoundError('AI-Skills packaged skill definitions are missing')


def skill_path(name: str) -> Traversable:
    if name not in SKILL_NAMES:
        raise ValueError(f'Unknown maintained skill: {name!r}')
    path = skill_root().joinpath(name, 'SKILL.md')
    if not path.is_file():
        raise FileNotFoundError(f'Missing maintained skill: {name}')
    return path
