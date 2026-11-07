"""Conservative, offline compatibility checks for pinned runtime dependencies.

Only numeric release versions are accepted. Unsupported PEP 440 forms fail
closed rather than letting pip install a pin that violates the target metadata.
"""
import re


def _release(version):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', version):
        raise ValueError(f'unsupported dependency version: {version}')
    return tuple(int(part) for part in version.split('.'))


def _version_key(release):
    parts = list(release)
    while len(parts) > 1 and parts[-1] == 0: parts.pop()
    return tuple(parts)


def _clause(specifier):
    match = re.fullmatch(r'(==|!=|>=|<=|>|<|~=)\s*([0-9]+(?:\.[0-9]+)*)(\.\*)?', specifier.strip())
    if not match:
        raise ValueError(f'unsupported dependency specifier: {specifier}')
    operator, text, wildcard = match.groups()
    release = _release(text)
    if (wildcard and operator not in ('==', '!=')) or (operator == '~=' and len(release) < 2):
        raise ValueError(f'unsupported dependency specifier: {specifier}')
    return operator, release, bool(wildcard)


def _matches(version, clause):
    operator, target, wildcard = clause
    current, wanted = _version_key(version), _version_key(target)
    if wildcard:
        padded = version + (0,) * max(0, len(target) - len(version))
        equal = padded[:len(target)] == target
        return equal if operator == '==' else not equal
    if operator == '~=':
        upper = (*target[:-2], target[-2] + 1)
        return current >= wanted and current < _version_key(upper)
    return {'==': current == wanted, '!=': current != wanted,
            '>=': current >= wanted, '<=': current <= wanted,
            '>': current > wanted, '<': current < wanted}[operator]


def _satisfies(version, specifier):
    specifier = specifier.strip()
    if not specifier: return True
    if specifier.startswith('(') and specifier.endswith(')'):
        specifier = specifier[1:-1]
    # Parse every clause before evaluating, including after a false clause.
    clauses = [_clause(part) for part in specifier.split(',')]
    release = _release(version)
    return all(_matches(release, clause) for clause in clauses)
