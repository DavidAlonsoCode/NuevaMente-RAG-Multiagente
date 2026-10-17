#!/usr/bin/env bash
# --validate is the default; publication requires --activate explicitly.
set -Eeuo pipefail
set +x
umask 077
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/python3 -I "$script_dir/verify_deployment.py" deploy "$@"
