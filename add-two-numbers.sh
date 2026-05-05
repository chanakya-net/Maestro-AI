#!/usr/bin/env bash

set -euo pipefail

is_number() {
  [[ "$1" =~ ^[+-]?([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]]
}

if [[ "$#" -ne 2 ]]; then
  echo "error: expected exactly two numeric arguments" >&2
  exit 1
fi

left="$1"
right="$2"

if ! is_number "${left}" || ! is_number "${right}"; then
  echo "error: both inputs must be numeric" >&2
  exit 1
fi

awk -v left="${left}" -v right="${right}" 'BEGIN { printf "%.15g\n", left + right }'
