#!/bin/sh
# /opt/houses/deploy-allowlist.sh — the command= dispatcher for the
# restricted deploy key (installed root-owned by box-setup.sh; the
# authorized_keys entry is written by install-deploy-allowlist.sh).
#
# Runs as the ubuntu user with $SSH_ORIGINAL_COMMAND = the deploy
# workflow's exact requested command line (man sshd → authorized_keys).
# Accepts ONLY the sanctioned shapes below; anything else is a silent
# no-op exit 0 (the workflow's forced-command fallback must never look
# like success with a side effect). Sanctioned commands then run through
# sudo (the sudoers allowlist: release.sh/switch.sh/journalctl).
#
# Injection discipline: no eval of $SSH_ORIGINAL_COMMAND. Every token is
# rebuilt from validated fields; refs and unit names pass explicit
# charset checks; numbers are digits-only. An unexpected token aborts.
set -eu

CMD="${SSH_ORIGINAL_COMMAND:-}"

# --- release.sh <ref> -------------------------------------------------
case "$CMD" in
  "sudo /opt/houses/release.sh "?*)
    REF=${CMD#"sudo /opt/houses/release.sh "}
    case "$REF" in
      *[!A-Za-z0-9._/-]*|"") echo "deploy-allowlist: bad ref" >&2; exit 1 ;;
      *) exec sudo /opt/houses/release.sh "$REF" ;;
    esac
    ;;
esac

# --- switch.sh [bare | --rollback | --diagnose | --publish] -------------
case "$CMD" in
  "sudo /opt/houses/switch.sh"|"sudo /opt/houses/switch.sh --rollback"|"sudo /opt/houses/switch.sh --diagnose"|"sudo /opt/houses/switch.sh --publish")
    exec sudo /opt/houses/switch.sh ${CMD#sudo /opt/houses/switch.sh}
    ;;
esac

# --- journalctl (read-only: fixed flag set, validated tokens) ---------
case "$CMD" in
  "sudo journalctl "?*)
    CMD=${CMD#sudo }
    # tokens in any order, each at most once: -u UNIT, -n N, --no-pager
    unit=""; count=""; pager=""
    state=args
    for tok in $CMD; do
      case "$state:$tok" in
        args:journalctl) ;;
        args:-u) state=unit ;;
        unit:*)
          case "$tok" in
            houses-blue|houses-green|houses-chrome|houses-network-watchdog|houses-network-watchdog.timer|houses-scrape-worker|houses-scrape-worker-dev)
              unit="$tok"; state=args ;;
            *) echo "deploy-allowlist: bad unit '$tok'" >&2; exit 1 ;;
          esac
          ;;
        args:-n) state=count ;;
        count:*)
          case "$tok" in
            *[!0-9]*) echo "deploy-allowlist: bad count '$tok'" >&2; exit 1 ;;
            *) count="$tok"; state=args ;;
          esac
          ;;
        args:--no-pager)
          [ -n "$pager" ] && { echo "deploy-allowlist: duplicate --no-pager" >&2; exit 1; }
          pager=1
          ;;
        *) echo "deploy-allowlist: unexpected journalctl token '$tok'" >&2; exit 1 ;;
      esac
    done
    # rebuild the argv from validated pieces (POSIX sh, no arrays)
    set -- journalctl
    [ -n "$unit" ] && set -- "$@" -u "$unit"
    [ -n "$count" ] && set -- "$@" -n "$count"
    [ -n "$pager" ] && set -- "$@" --no-pager
    exec sudo "$@"
    ;;
esac

# --- everything else: silent no-op (exit 0, no side effects) -----------
exit 0