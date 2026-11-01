"""The maintained runtime and its test harness have one portable implementation."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def test_no_legacy_executable_sources_remain():
    files = [*ROOT.glob('*.sh'), *ROOT.glob('*.ps1'), *ROOT.glob('assets/*.sh'),
             *ROOT.glob('assets/*.ps1'), *ROOT.glob('assets/*.py'),
             *ROOT.glob('tests/*.sh'), *ROOT.glob('tests/*.ps1')]
    assert files == [], [str(path.relative_to(ROOT)) for path in files]


def test_current_public_docs_use_native_runtime():
    pattern = re.compile(r'(?:run-agent|worker-watch|pair-colleague|run-with-it-[\w-]+)\.(?:sh|ps1|py)\b|(?:un)?install\.(?:sh|ps1)\b')
    for path in [ROOT / 'README.md', ROOT / 'explainer.html']:
        assert not pattern.search(path.read_text(encoding='utf-8')), str(path)


def test_legacy_record_schemas_remain_explicit_for_guarded_migration():
    records = json.loads((ROOT / 'tests/contracts/legacy_records.json').read_text())['records']
    for name, schema in {'main-state': 4, 'sub-state': 1, 'worker-state': 1,
                         'pair-state': 2, 'pair-config': 2}.items():
        assert records[name]['schema_version'] == schema
