"""Add two decimal numbers without a platform-specific shell or utility."""
from decimal import Decimal
import re
import sys

NUMBER = re.compile(r'-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z')


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 2:
        print('error: expected exactly two numeric arguments', file=sys.stderr)
        return 1
    if not all(NUMBER.fullmatch(value) for value in args):
        print('error: both inputs must be numeric', file=sys.stderr)
        return 1
    result = format(Decimal(args[0]) + Decimal(args[1]), 'f')
    print(result.rstrip('0').rstrip('.') if '.' in result else result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
