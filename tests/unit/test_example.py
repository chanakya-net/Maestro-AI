import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'examples/add_two_numbers.py'


@pytest.mark.parametrize('args,code,output', [
    (['2', '3'], 0, '5'), (['.1', '.2'], 0, '0.3'),
    (['-1.25', '1.25'], 0, '0'), (['-2', '.5'], 0, '-1.5'),
    (['1'], 1, 'expected exactly two'), (['nan', '2'], 1, 'must be numeric'),
    (['1e2', '2'], 1, 'must be numeric')])
def test_addition_example(args, code, output, capsys):
    spec = importlib.util.spec_from_file_location('addition_example', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(args) == code
    captured = capsys.readouterr()
    assert output in (captured.out if code == 0 else captured.err)
