#!/usr/bin/env bash
# sandbox_exec.sh -- run a command with parts of the host filesystem hidden from it.
#
#   HIDE_PATHS="/path/one:/path/two:..." [FRESH_PATHS="/tmp/claude-0:..."]
#   [EXPOSE_SRC=/real/run/dir EXPOSE_DST=/neutral/mount] [CHDIR=/dir/inside] sandbox_exec.sh CMD [ARGS...]
#
# FRESH_PATHS are directories the child needs to write to but must not see the host's contents
# of (the CLI's own temp directory, /tmp/claude-<uid>, which on a shared machine holds the parent
# session's scratch files): each gets an empty, writable tmpfs instead of a sealed one.
#
# Creates a private user + mount namespace (no root privilege needed beyond what the caller
# has), bind-mounts EXPOSE_SRC onto EXPOSE_DST (so the run sees itself at a fixed neutral path
# and never sees the sibling runs next to its real directory), mounts an empty tmpfs over every
# directory in HIDE_PATHS and /dev/null over every file in it, drops every capability (so the
# child cannot unmount what was hidden), changes into CHDIR and execs CMD. Nothing on the host
# changes; the hiding exists only inside the child's process tree.
#
# Purpose: the discovery-loop reruns must not be able to find the original run, the paper, the
# parent session's files, or the other arms on the same machine. The pilot agent ran
# `find / -name ...` and listed every arm directory and the as-run analysis directory; it chose
# not to read them, which is luck, not design. A clean container per run is the stronger
# option; this wrapper is the fallback when the runs share a machine with anything
# answer-bearing.
set -euo pipefail
if ! unshare -U -r -m true 2>/dev/null; then
  echo "sandbox_exec: user namespaces unavailable; refusing to run unsandboxed (set SANDBOX=0 to override)" >&2
  exit 9
fi
export HIDE_PATHS="${HIDE_PATHS:-}" FRESH_PATHS="${FRESH_PATHS:-}" EXPOSE_SRC="${EXPOSE_SRC:-}" EXPOSE_DST="${EXPOSE_DST:-}" CHDIR="${CHDIR:-}"
exec unshare -U -r -m --propagation private bash -c '
set -e
if [ -n "$EXPOSE_SRC" ]; then
  [ -d "$EXPOSE_DST" ] || { echo "sandbox_exec: EXPOSE_DST $EXPOSE_DST must exist (an empty directory on the host)" >&2; exit 8; }
  mount --bind "$EXPOSE_SRC" "$EXPOSE_DST"
fi
IFS=: read -r -a paths <<< "$HIDE_PATHS"
for p in "${paths[@]}"; do
  [ -n "$p" ] || continue
  if [ -d "$p" ]; then mount -t tmpfs -o size=64k,nr_inodes=4 none "$p"
  elif [ -e "$p" ]; then mount --bind /dev/null "$p"
  fi
done
IFS=: read -r -a fresh <<< "$FRESH_PATHS"
for p in "${fresh[@]}"; do
  [ -n "$p" ] || continue
  mkdir -p "$p" 2>/dev/null || true
  [ -d "$p" ] && mount -t tmpfs -o size=4g none "$p"
done
[ -z "$CHDIR" ] || cd "$CHDIR"
exec setpriv --bounding-set -all --inh-caps -all --no-new-privs -- "$@"
' sandbox_exec "$@"
