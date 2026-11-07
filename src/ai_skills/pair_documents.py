"""Strict Markdown contracts and frozen pair discussion snapshots."""


from __future__ import annotations


import datetime
import hashlib
import os
import re
import tempfile
from typing import Any
from pathlib import Path

from ai_skills.storage import replace_file_atomic


AGENT_SECTIONS = [
    "Candidate analysis",
    "Preferred solution",
    "Improvements and implementation detail",
    "Agreements",
    "Disagreements",
    "Risks and unresolved questions",
    "Suggested next step",
]


DECISION_SECTIONS = [
    "Round assessment",
    "Candidate-solution ledger",
    "Current synthesis or preferred direction",
    "Agreements",
    "Disagreements",
    "Rejected candidates",
    "Unresolved questions and risks",
    "Focus for next round",
    "Decision rationale",
]


DOSSIER_SECTIONS = [
    "Executive summary",
    "Problem and success criteria",
    "Constraints and assumptions",
    "Candidate solutions considered",
    "Trade-off comparison",
    "Selected or synthesized solution",
    "Detailed design and operation",
    "How this solves the problem",
    "Implementation approach",
    "Decision rationale",
    "Agent agreement",
    "Remaining disagreement and coordinator resolution",
    "Rejected alternatives",
    "Risks and mitigations",
    "Open questions and human decisions",
    "Discussion record",
]


AGENT_STATUS_VALUES = ("CONTINUE", "READY")


DECISION_ACTION_VALUES = ("CONTINUE", "ACCEPT")


PLACEHOLDER_LINE = re.compile(r"^<[^<>]{3,}>$")


PLACEHOLDER_WORDS = re.compile(r"\b(TODO|TBD|FIXME)\b")


UNRESOLVED_TEMPLATE = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")


INLINE_CODE = re.compile(r"`+[^`]*`+")


DISCUSSION_RECORD_SECTION = "Discussion record"


DOSSIER_RUN_LABELS = (
    ("Completed rounds", "completed_rounds"),
    ("Minimum rounds", "min_rounds"),
    ("Maximum rounds", "max_rounds"),
    ("Completion reason", "completion_reason"),
    ("Run directory", "run_dir"),
)


class HelperError(Exception):
    """A user-facing failure; the message is printed without a traceback."""


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_lines(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.replace("\r\n", "\n").split("\n")]


FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def fence_mask(lines: list[str]) -> list[bool]:
    """Mark every line that belongs to a fenced code block.

    Markdown inside a fence is an example, not document structure: a template
    shown in a ```markdown block must never satisfy — or violate — a heading,
    status-line, metadata, or placeholder rule.
    """
    mask = [False] * len(lines)
    fence_char = ""
    fence_len = 0
    for index, line in enumerate(lines):
        match = FENCE_OPEN.match(line)
        token = match.group(1) if match else ""
        if not fence_char:
            if token:
                fence_char, fence_len = token[0], len(token)
                mask[index] = True
            continue
        mask[index] = True
        if token and token[0] == fence_char and len(token) >= fence_len and line.strip() == token:
            fence_char, fence_len = "", 0
    return mask


def structural(text: str) -> tuple[list[str], list[bool]]:
    lines = normalize_lines(text)
    return lines, fence_mask(lines)


def heading_occurrences(
    lines: list[str], level: int, title: str, mask: list[bool] | None = None
) -> list[int]:
    marker = "#" * level + " " + title
    if mask is None:
        mask = fence_mask(list(lines))
    return [
        index
        for index, line in enumerate(lines)
        if not mask[index] and line.strip() == marker
    ]


def extract_section(text: str, level: int, title: str) -> str:
    """Return the body under the first `title` heading at `level`."""
    lines, mask = structural(text)
    positions = heading_occurrences(lines, level, title, mask)
    if not positions:
        return ""
    start = positions[0] + 1
    prefix = "#" * level + " "
    end = len(lines)
    for index in range(start, len(lines)):
        if mask[index]:
            continue
        stripped = lines[index].strip()
        if stripped.startswith(prefix) or re.match(r"^#{1,%d} \S" % level, stripped):
            end = index
            break
    return "\n".join(lines[start:end]).strip("\n")


def trailing_status(lines: list[str], key: str) -> tuple[str, list[str]]:
    """Return (value, errors) for the trailing `KEY: VALUE` contract line."""
    errors: list[str] = []
    mask = fence_mask(list(lines))
    status_indexes = [
        index
        for index, line in enumerate(lines)
        if not mask[index] and line.strip().startswith(f"{key}:")
    ]
    if not status_indexes:
        errors.append(f"missing required final line '{key}: <value>'")
        return "", errors
    if len(status_indexes) > 1:
        errors.append(f"found {len(status_indexes)} '{key}' lines; exactly one is allowed")
        return "", errors

    index = status_indexes[0]
    raw = lines[index].strip()
    value = raw[len(key) + 1 :].strip()
    for tail in lines[index + 1 :]:
        if tail.strip():
            errors.append(
                f"unexpected text after the '{key}' line: {tail.strip()[:80]!r}"
            )
            break
    return value, errors


def section_errors(text: str, level: int, titles: list[str]) -> list[str]:
    lines, mask = structural(text)
    errors: list[str] = []
    last_position = -1
    for title in titles:
        positions = heading_occurrences(lines, level, title, mask)
        if not positions:
            errors.append(f"missing required section '{'#' * level} {title}'")
            continue
        if len(positions) > 1:
            errors.append(
                f"section '{'#' * level} {title}' appears {len(positions)} times; "
                "exactly one is required"
            )
            continue
        if positions[0] < last_position:
            errors.append(f"section '{'#' * level} {title}' is out of order")
        last_position = positions[0]
    return errors


def empty_section_errors(text: str, level: int, titles: list[str]) -> list[str]:
    errors = []
    for title in titles:
        if not extract_section(text, level, title).strip():
            errors.append(f"section '{'#' * level} {title}' is empty")
    return errors


def placeholder_errors(text: str) -> list[str]:
    """Flag unfilled template text outside fenced examples.

    Fenced blocks are excluded so a document may legitimately quote the
    template it was built from, and marker words are matched on word
    boundaries so ordinary technical prose is not rejected.
    """
    errors = []
    lines, mask = structural(text)
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        stripped = line.strip()
        if PLACEHOLDER_LINE.match(stripped):
            errors.append(f"unfilled placeholder text: {stripped[:80]!r}")
        # An inline code span quotes syntax rather than leaving it unfilled, so
        # prose may discuss `{{AGENT_NAME}}` or a `TODO` marker by name.
        prose = INLINE_CODE.sub(" ", stripped)
        match = PLACEHOLDER_WORDS.search(prose)
        if match:
            errors.append(f"placeholder marker {match.group(1)!r} in: {stripped[:80]!r}")
        if UNRESOLVED_TEMPLATE.search(prose):
            errors.append(f"unresolved template variable in: {stripped[:80]!r}")
    return errors


def validate_agent_output(path: str) -> list[str]:
    text = read_text(path)
    if not text.strip():
        return ["agent produced no output"]
    errors = section_errors(text, 1, AGENT_SECTIONS)
    errors += empty_section_errors(text, 1, AGENT_SECTIONS)
    errors += placeholder_errors(text)
    value, status_errors = trailing_status(normalize_lines(text), "DISCUSSION_STATUS")
    errors += status_errors
    if value and value not in AGENT_STATUS_VALUES:
        errors.append(
            f"DISCUSSION_STATUS must be one of {', '.join(AGENT_STATUS_VALUES)}; got {value!r}"
        )
    return errors


def validate_decision(path: str) -> list[str]:
    text = read_text(path)
    if not text.strip():
        return ["coordinator decision is empty"]
    errors = section_errors(text, 1, DECISION_SECTIONS)
    errors += empty_section_errors(text, 1, DECISION_SECTIONS)
    errors += placeholder_errors(text)
    value, status_errors = trailing_status(normalize_lines(text), "COORDINATOR_ACTION")
    errors += status_errors
    if value and value not in DECISION_ACTION_VALUES:
        errors.append(
            f"COORDINATOR_ACTION must be one of {', '.join(DECISION_ACTION_VALUES)}; got {value!r}"
        )
    return errors


def metadata_values(text: str, label: str) -> list[str]:
    """Every `- Label: value` line in `text`, outside fenced examples."""
    pattern = re.compile(r"^\s*[-*]\s*" + re.escape(label) + r"\s*:\s*(.+?)\s*$")
    lines, mask = structural(text)
    found = []
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        match = pattern.match(line)
        if match:
            found.append(match.group(1).strip())
    return found


def agent_identity_errors(record: str, slot: str, agent: dict[str, str]) -> list[str]:
    """The dossier must name each colleague's real configuration, not a guess."""
    label = f"Agent {slot}"
    values = metadata_values(record, label)
    if not values:
        return [f"Discussion record is missing the '{label}' line"]
    if len(values) > 1:
        return [
            f"Discussion record has {len(values)} '{label}' lines; exactly one is required"
        ]

    expected = [agent.get(key, "") for key in ("name", "harness", "model", "effort")]
    if agent.get("requested_effort"):
        expected.append(f"requested effort {agent['requested_effort']}")
    fields = [field.strip() for field in values[0].split(",") if field.strip()]
    if sorted(fields) != sorted(expected):
        return [
            f"Discussion record '{label}' is {values[0]!r} but the run configured "
            + ", ".join(expected)
        ]
    return []


def validate_dossier(
    path: str, expected: dict[str, str], config: dict[str, Any] | None = None
) -> list[str]:
    text = read_text(path)
    if not text.strip():
        return ["final dossier is empty"]

    lines, mask = structural(text)
    errors: list[str] = []
    if not heading_occurrences(lines, 1, "Final Solution Decision", mask):
        errors.append("missing required title '# Final Solution Decision'")
    errors += section_errors(text, 2, DOSSIER_SECTIONS)
    errors += empty_section_errors(text, 2, DOSSIER_SECTIONS)
    errors += placeholder_errors(text)

    # Provenance lives inside the Discussion record and nowhere else, so a
    # matching line quoted in an example elsewhere cannot satisfy the contract.
    record = extract_section(text, 2, DISCUSSION_RECORD_SECTION)

    for label, key in DOSSIER_RUN_LABELS:
        values = metadata_values(record, label)
        if not values:
            errors.append(f"Discussion record is missing the '{label}' line")
        elif len(values) > 1:
            errors.append(
                f"Discussion record has {len(values)} '{label}' lines; exactly one is required"
            )
        elif values[0] != expected[key]:
            errors.append(
                f"Discussion record '{label}' is {values[0]!r} but the run recorded "
                f"{expected[key]!r}"
            )

    if len(metadata_values(record, "Coordinator")) != 1:
        errors.append("Discussion record must carry exactly one 'Coordinator' line")

    agents = (config or {}).get("agents", {}) if isinstance(config, dict) else {}
    for slot in ("1", "2"):
        agent = agents.get(slot)
        if isinstance(agent, dict):
            errors += agent_identity_errors(record, slot, agent)
        elif len(metadata_values(record, f"Agent {slot}")) != 1:
            errors.append(
                f"Discussion record must carry exactly one 'Agent {slot}' line"
            )

    return errors


def round_dir(run_dir: str, number: int) -> str:
    return os.path.join(run_dir, "rounds", f"round-{number:02d}")


CONSTRAINTS_HEADING = re.compile(
    r"^#{1,6}\s+.*(constraint|success criteri|acceptance criteri)", re.I
)


NO_CONSTRAINTS_BLOCK = (
    "As stated in the original problem above. No separate constraint or "
    "success-criteria block was supplied."
)


def split_task(task_text: str) -> tuple[str, str]:
    """Split the task into (problem, constraints).

    When the task carries its own constraints/success-criteria heading, that
    block moves into the snapshot's constraints section instead of being
    repeated under both headings. task.md on disk is never modified.
    """
    lines = normalize_lines(task_text)
    for index, line in enumerate(lines):
        if not CONSTRAINTS_HEADING.match(line.strip()):
            continue
        level = len(line) - len(line.lstrip("#"))
        end = len(lines)
        for offset, tail in enumerate(lines[index + 1 :], start=index + 1):
            stripped = tail.strip()
            if stripped.startswith("#"):
                tail_level = len(stripped) - len(stripped.lstrip("#"))
                if tail_level <= level:
                    end = offset
                    break
        constraints = "\n".join(lines[index + 1 : end]).strip("\n")
        problem = "\n".join(lines[:index] + lines[end:]).strip("\n")
        if constraints.strip() and problem.strip():
            return problem, constraints
    return task_text.strip("\n"), NO_CONSTRAINTS_BLOCK


def build_round_one_snapshot(run_dir: str) -> str:
    problem, constraints = split_task(read_text(os.path.join(run_dir, "task.md")))
    return "\n".join(
        [
            "# Original problem",
            "",
            problem,
            "",
            "# Constraints and success criteria",
            "",
            constraints,
            "",
            "# Coordinator focus for this round",
            "",
            "This is independent exploration. Develop your own candidate solutions from the "
            "problem alone. Propose at least two materially distinct credible solutions when "
            "the problem admits alternatives, and explain why alternatives are not viable when "
            "only one credible approach exists. There is no prior discussion, no shared ledger, "
            "and no preferred direction yet.",
            "",
        ]
    )


def build_next_snapshot(run_dir: str, target_round: int) -> str:
    previous = target_round - 1
    previous_dir = round_dir(run_dir, previous)
    decision_path = os.path.join(previous_dir, "coordinator-decision.md")
    if not os.path.exists(decision_path):
        raise HelperError(f"missing coordinator decision for round {previous}")

    decision = read_text(decision_path)
    problem, constraints = split_task(read_text(os.path.join(run_dir, "task.md")))

    def decided(title: str, fallback: str = "None") -> str:
        body = extract_section(decision, 1, title).strip("\n")
        return body if body.strip() else fallback

    agent_1 = read_text(os.path.join(previous_dir, "agent-1-output.md")).strip("\n")
    agent_2 = read_text(os.path.join(previous_dir, "agent-2-output.md")).strip("\n")

    return "\n".join(
        [
            "# Original problem",
            "",
            problem,
            "",
            "# Constraints and success criteria",
            "",
            constraints,
            "",
            "# Candidate-solution ledger",
            "",
            decided("Candidate-solution ledger"),
            "",
            "# Current synthesis or preferred direction",
            "",
            decided("Current synthesis or preferred direction", "Not selected"),
            "",
            "# Agreements",
            "",
            decided("Agreements"),
            "",
            "# Disagreements",
            "",
            decided("Disagreements"),
            "",
            "# Rejected candidates",
            "",
            decided("Rejected candidates"),
            "",
            "# Unresolved questions and risks",
            "",
            decided("Unresolved questions and risks"),
            "",
            "# Coordinator focus for this round",
            "",
            decided("Focus for next round"),
            "",
            "# Previous round: Agent 1",
            "",
            agent_1,
            "",
            "# Previous round: Agent 2",
            "",
            agent_2,
            "",
        ]
    )


def render_template(template_path: str, output_path: str, variables: list[str]) -> None:
    text = read_text(template_path)
    for pair in variables:
        if "=" not in pair:
            raise HelperError(f"--var expects NAME=value, got {pair!r}")
        name, value = pair.split("=", 1)
        text = text.replace("{{" + name + "}}", value)
    leftover = sorted(set(re.findall(r"\{\{[A-Za-z0-9_]+\}\}", text)))
    if leftover:
        raise HelperError(
            "unresolved template variables would be sent to the runner: "
            + ", ".join(leftover)
        )
    write_text_atomic(output_path, text)


def write_text_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix="." + path.name, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file_atomic(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def freeze_snapshot(run_dir: str | Path, number: int) -> str:
    text = build_round_one_snapshot(str(run_dir)) if number == 1 else build_next_snapshot(str(run_dir), number)
    target = Path(round_dir(str(run_dir), number)) / "snapshot.md"
    if target.exists() and target.read_bytes() != text.encode("utf-8"):
        raise HelperError(f"the round {number} snapshot already exists with different content: {target}; its inputs changed after it was built")
    if not target.exists():
        write_text_atomic(target, text)
    digest = sha256_file(target)
    write_text_atomic(target.with_suffix(".sha256"), digest + "\n")
    return digest
