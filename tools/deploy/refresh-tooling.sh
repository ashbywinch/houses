#!/bin/sh
# /opt/houses/refresh-tooling.sh <artifact-tree>
#
# Installs the box's rollout tooling (the /opt/houses scripts, the migrations
# list and the systemd units) from a staged artifact tree. This is the NARROW
# alternative to running the machine bootstrap (box-setup.sh) on a box that is
# already serving — which is what "refresh the scripts" used to do, and what
# took production down on 2026-10-29: the bootstrap stops at nothing, so a
# script refresh also rewrote sudoers, re-enabled units and re-chowned the app
# tree, while the box was meant to stay a working rollback target.
#
# CONTRACT (a change that breaks any line here is a regression):
#   * NO-OP when every file already matches: no write, no systemd call.
#   * NEVER stops, starts, restarts, enables or disables a service.
#   * NEVER touches the app tree, the database, sudoers, or group membership.
#   * Every file is validated BEFORE anything is replaced.
#   * Each replacement is atomic (write .new in place, mv over) and the previous
#     copy is kept in $ROOT/.tooling-previous/.
#   * `systemctl daemon-reload` runs only when a unit file actually changed.
set -eu

ROOT="${HOUSES_ROOT:-/opt/houses}"
SRC="${1:?usage: refresh-tooling.sh <artifact-tree> <sha256> [ref]}"
SHA="${2:?usage: refresh-tooling.sh <artifact-tree> <sha256> [ref]}"
REF="${3:-unknown}"
BACKUP="$ROOT/.tooling-previous"

[ "$(id -u)" = 0 ] || { echo "refresh-tooling: run as root" >&2; exit 1; }
[ -d "$SRC/tools/deploy" ] || { echo "refresh-tooling: no tooling at $SRC/tools/deploy" >&2; exit 1; }

# path:mode — every file this refresher owns, and nothing else.
FILES="
run-instance.sh:0755
switch.sh:0755
run_migrations.py:0755
install-artifact.sh:0755
deploy-allowlist.sh:0755
install-deploy-allowlist.sh:0755
network-watchdog.sh:0755
refresh-tooling.sh:0755
migrations.list:0644
"
UNITS="
houses.service:0644
houses-network-watchdog.service:0644
houses-network-watchdog.timer:0644
"

echo "refresh-tooling: validating $SRC"
for entry in $FILES; do
  name=${entry%%:*}
  [ -f "$SRC/tools/deploy/$name" ] || { echo "refresh-tooling: missing $name in the artifact" >&2; exit 1; }
  case "$name" in
    *.sh)
      # Parse with the interpreter the file declares: switch.sh and
      # install-artifact.sh are bash (process substitution), the rest are sh —
      # checking a bash script with `sh -n` rejects a valid artifact.
      case "$(head -1 "$SRC/tools/deploy/$name")" in
        *bash*) bash -n "$SRC/tools/deploy/$name" || { echo "refresh-tooling: $name failed bash -n — nothing written" >&2; exit 1; } ;;
        *) sh -n "$SRC/tools/deploy/$name" || { echo "refresh-tooling: $name failed sh -n — nothing written" >&2; exit 1; } ;;
      esac
      ;;
    *.py) python3 -m py_compile "$SRC/tools/deploy/$name" || { echo "refresh-tooling: $name failed py_compile — nothing written" >&2; exit 1; } ;;
  esac
done
for entry in $UNITS; do
  name=${entry%%:*}
  [ -f "$SRC/tools/deploy/units/$name" ] || { echo "refresh-tooling: missing unit $name in the artifact" >&2; exit 1; }
done
echo "refresh-tooling: validation ok"

mkdir -p "$BACKUP"

installed=0
install_one() {
  # install_one <source> <destination> <mode>
  src=$1 dst=$2 mode=$3
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    return 0
  fi
  [ ! -f "$dst" ] || cp -p "$dst" "$BACKUP/$(basename "$dst")" 2>/dev/null || true
  # Running as root (checked above), so the new file is root-owned already.
  install -m "$mode" "$src" "$dst.new"
  mv -f "$dst.new" "$dst"
  installed=$((installed + 1))
  echo "refresh-tooling: updated $(basename "$dst")"
}

for entry in $FILES; do
  name=${entry%%:*}; mode=${entry##*:}
  install_one "$SRC/tools/deploy/$name" "$ROOT/$name" "$mode"
done

units_changed=0
UNIT_DIR="${HOUSES_UNIT_DIR:-/etc/systemd/system}"
for entry in $UNITS; do
  name=${entry%%:*}; mode=${entry##*:}
  before=$installed
  install_one "$SRC/tools/deploy/units/$name" "$UNIT_DIR/$name" "$mode"
  [ "$installed" = "$before" ] || units_changed=1
done

if [ "$units_changed" = 1 ]; then
  # daemon-reload only: unit FILES changed. No enable, no start, no restart —
  # the running services keep running (a unit-file change applies at their next
  # start, which the rollout controls).
  systemctl daemon-reload
fi

if [ "$installed" = 0 ]; then
  # A no-op is a no-op: not even the provenance marker is rewritten, so a
  # release that ships no tooling change writes nothing at all to this box.
  echo "refresh-tooling: already current at $SHA — nothing written"
  exit 0
fi

printf 'sha256=%s ref=%s installed_at=%s\n' \
  "$SHA" "$REF" "$(date -u +%FT%TZ)" > "$ROOT/TOOLING"
chmod 644 "$ROOT/TOOLING"
echo "refresh-tooling: $installed file(s) updated (previous copies in $BACKUP)"
