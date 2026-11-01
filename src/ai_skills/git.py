"""Git evidence queries and explicit salvage operations."""
from __future__ import annotations
import re
import subprocess

def git_output(repo_root: str, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo_root, *args],
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()

def git_success(repo_root: str, *args: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", repo_root, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )

def current_head(repo_root: str) -> str:
    return git_output(repo_root, "rev-parse", "HEAD")

def repo_available(repo_root: str) -> bool:
    try:
        return git_output(repo_root, "rev-parse", "--is-inside-work-tree") == "true"
    except Exception:
        return False

def working_tree_clean(repo_root: str) -> bool:
    try:
        return git_output(repo_root, "status", "--porcelain") == ""
    except Exception:
        return False

def commit_exists(repo_root: str, commit_sha: str) -> bool:
    return git_success(repo_root, "cat-file", "-e", f"{commit_sha}^{{commit}}")

def resolve_commit(repo_root: str, revision: str) -> str | None:
    """Resolve one revision to its canonical commit object ID."""
    if not revision or revision == "NONE":
        return None
    try:
        resolved = git_output(repo_root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    except Exception:
        return None
    return resolved if re.fullmatch(r"[0-9a-fA-F]{40}", resolved) else None

def is_ancestor(repo_root: str, ancestor: str, descendant: str) -> bool:
    return git_success(repo_root, "merge-base", "--is-ancestor", ancestor, descendant)

def abort_cherry_pick(repo_root: str) -> None:
    git_success(repo_root, "cherry-pick", "--abort")

def commit_salvaged_tree(repo_root: str, issue: str, role: str) -> bool:
    """Commit a worker's uncommitted work so a stall/kill does not lose it."""
    if not git_success(repo_root, "add", "-A"):
        return False
    message = f"salvage(#{issue}): recover {role} work from interrupted worker"
    return git_success(repo_root, "commit", "--no-verify", "-m", message)
