#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "error: expected exactly two numeric arguments" >&2
  exit 1
fi

number_re='^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
if [[ ! "$1" =~ ${number_re} || ! "$2" =~ ${number_re} ]]; then
  echo "error: both inputs must be numeric" >&2
  exit 1
fi

awk -v a="$1" -v b="$2" 'BEGIN {
  result = a + b
  if (result == int(result)) {
    printf "%d\n", result
  } else {
    printf "%s\n", result
  }
}'
