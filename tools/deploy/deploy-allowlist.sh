#!/bin/sh
# /opt/houses/deploy-allowlist.sh — the command= dispatcher for the restricted
# deploy key (installed root-owned by box-setup.sh; the authorized_keys entry is
# written by install-deploy-allowlist.sh).
#
# Runs as the ubuntu user with $SSH_ORIGINAL_COMMAND = the deploy workflow's
# exact requested command line (man sshd → authorized_keys). Accepts ONLY the
# sanctioned shapes below; anything else is a silent no-op exit 0 (the
# workflow's forced-command fallback must never look like success with a side
# effect). Sanctioned commands then run through sudo (the sudoers allowlist:
# install-artifact.sh, switch.sh, journalctl).
#
# Injection discipline: no eval of $SSH_ORIGINAL_COMMAND. Every token is rebuilt
# from validated fields; the artifact object and the restore source are
# charset-checked, the row count is digits-only. An unexpected token aborts.
#
# The sanctioned set is exactly what release.yml calls and no more (R7): including
# a shape CI does not use would be a capability nothing asked for.
set -eu

CMD="${SSH_ORIGINAL_COMMAND:-}"
SHA256_HEX_CHARS=64   # the artifact's key IS its sha256, in hex

# --- the rollout install: install-artifact.sh [--tooling-only] <gs://...tar.gz>
# The OBJECT is the content-addressed artifact name — the script re-verifies
# the sha256 against the key it fetches. --tooling-only (owner-side refresh,
# no app work) is the one allowed prefix.
case "$CMD" in
  "sudo /opt/houses/install-artifact.sh "*|"sudo /opt/houses/install-artifact.sh --tooling-only "*)
    case "$CMD" in
      "sudo /opt/houses/install-artifact.sh --tooling-only "*) OBJECT=${CMD#"sudo /opt/houses/install-artifact.sh --tooling-only "} ;;
      *) OBJECT=${CMD#"sudo /opt/houses/install-artifact.sh "} ;;
    esac
    case "$OBJECT" in
      gs://*/*.tar.gz)
        rest=${OBJECT#gs://}
        bucket=${rest%%/*}
        key=${rest#*/}
        case "$bucket" in
          *[!A-Za-z0-9._-]*|"") echo "deploy-allowlist: bad bucket" >&2; exit 1 ;;
        esac
        case "$key" in
          *.tar.gz) HASH=${key%.tar.gz} ;;
          *) echo "deploy-allowlist: the artifact must be <sha256>.tar.gz" >&2; exit 1 ;;
        esac
        case "$HASH" in
          *[!0-9a-f]*|"") echo "deploy-allowlist: the artifact key must be a 64-character lowercase sha256 hex string" >&2; exit 1 ;;
        esac
        [ "${#HASH}" = "${SHA256_HEX_CHARS}" ] || { echo "deploy-allowlist: the artifact key must be a 64-character lowercase sha256 hex string" >&2; exit 1; }
        exec sudo /opt/houses/install-artifact.sh "$OBJECT"
        ;;
      *) echo "deploy-allowlist: bad artifact object" >&2; exit 1 ;;
    esac
    ;;
esac

# --- switch.sh --snapshot (FREEZE production, then the DB on stdout) ------
if [ "$CMD" = "sudo /opt/houses/switch.sh --snapshot" ]; then
  exec sudo /opt/houses/switch.sh --snapshot
fi

# --- switch.sh --unfreeze (the cutover's abort path: serve from here again) --
if [ "$CMD" = "sudo /opt/houses/switch.sh --unfreeze" ]; then
  exec sudo /opt/houses/switch.sh --unfreeze
fi

# --- switch.sh --rebase <rows> (snapshot on stdin, migrate, verify) --------
# The row count is REQUIRED: it is what proves the transfer did not lose rows, and
# a bare form would silently skip that comparison. `--restore` (below) is the
# path for a snapshot whose count nobody recorded.
case "$CMD" in
  "sudo /opt/houses/switch.sh --rebase "*)
    ROWS=${CMD#"sudo /opt/houses/switch.sh --rebase "}
    case "$ROWS" in
      *[!0-9]*|"") echo "deploy-allowlist: bad row count" >&2; exit 1 ;;
    esac
    exec sudo /opt/houses/switch.sh --rebase "$ROWS"
    ;;
esac

# --- switch.sh --restore <gs://…> (the exception path: data from an object) --
case "$CMD" in
  "sudo /opt/houses/switch.sh --restore "*)
    SOURCE=${CMD#"sudo /opt/houses/switch.sh --restore "}
    case "$SOURCE" in
      gs://*/*.db)
        rest=${SOURCE#gs://}
        bucket=${rest%%/*}
        key=${rest#*/}
        case "$bucket" in
          *[!A-Za-z0-9._-]*|"") echo "deploy-allowlist: bad bucket" >&2; exit 1 ;;
        esac
        case "$key" in
          *[!A-Za-z0-9._/-]*|"") echo "deploy-allowlist: bad restore object" >&2; exit 1 ;;
        esac
        exec sudo /opt/houses/switch.sh --restore "$SOURCE"
        ;;
      *) echo "deploy-allowlist: the restore source must be gs://<bucket>/<object>.db" >&2; exit 1 ;;
    esac
    ;;
esac

# --- switch.sh --smoke-relay <ip|off> (owner relays houses-smoke to the
# --- standby; off retires it post-flip — smoke must never mirror production)
if [ "$CMD" = "sudo /opt/houses/switch.sh --smoke-relay off" ]; then
  exec sudo /opt/houses/switch.sh --smoke-relay off
fi
case "$CMD" in
  "sudo /opt/houses/switch.sh --smoke-relay "*)
    IP=${CMD#"sudo /opt/houses/switch.sh --smoke-relay "}
    case "$IP" in
      *[!0-9.]*|"") echo "deploy-allowlist: bad relay IP" >&2; exit 1 ;;
    esac
    # the relay may only target the boxes' own VPC subnet (10.0.0.0/24) —
    # never an arbitrary internal address a compromised deploy key could pick
    case "$IP" in
      10.0.0.*) : ;;
      *) echo "deploy-allowlist: relay IP outside the VPC subnet" >&2; exit 1 ;;
    esac
    DOTS=$(printf '%s' "$IP" | tr -cd '.' | wc -c)
    [ "$DOTS" = 3 ] || { echo "deploy-allowlist: bad relay IP (need 4 octets)" >&2; exit 1; }
    case "$IP" in
      *.*.*.*.*|*..*|.*|*.) echo "deploy-allowlist: bad relay IP" >&2; exit 1 ;;
    esac
    exec sudo /opt/houses/switch.sh --smoke-relay "$IP"
    ;;
esac

# --- switch.sh --public-url — the two role URLs ONLY (never arbitrary) ----
if [ "$CMD" = "sudo /opt/houses/switch.sh --public-url https://houses-smoke.blueumbrella.net" ] || \
   [ "$CMD" = "sudo /opt/houses/switch.sh --public-url https://houses.blueumbrella.net" ]; then
  exec sudo /opt/houses/switch.sh --public-url "${CMD##*--public-url }"
fi

# --- switch.sh --role https://houses.blueumbrella.net (the flip's promote:
# role URL + app restart + health — the production URL only, never arbitrary) --
if [ "$CMD" = "sudo /opt/houses/switch.sh --role https://houses.blueumbrella.net" ]; then
  exec sudo /opt/houses/switch.sh --role https://houses.blueumbrella.net
fi

# --- switch.sh --serve (start the standby app for the review surface) -----
if [ "$CMD" = "sudo /opt/houses/switch.sh --serve" ]; then
  exec sudo /opt/houses/switch.sh --serve
fi

# --- switch.sh --diagnose (read-only state dump) --------------------------
if [ "$CMD" = "sudo /opt/houses/switch.sh --diagnose" ]; then
  exec sudo /opt/houses/switch.sh --diagnose
fi

# --- everything else: silent no-op (exit 0, no side effects) -----------
exit 0
