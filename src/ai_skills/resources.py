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
