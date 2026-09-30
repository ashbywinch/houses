#!/bin/bash
# The bake source ref: a content hash of the files the base image is baked
# FROM. The bake stamps it into the image description; the release chain
# re-bakes when main's current source no longer matches. ONE definition of
# "what the image is built from" — used by both the bake and the drift check.
set -euo pipefail
ROOT="${HOUSES_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
git -C "$ROOT" rev-parse :tools/deploy/base-image.sh :tools/deploy/box-setup.sh \
  | tr -d '\n' | sha256sum | cut -d' ' -f1
