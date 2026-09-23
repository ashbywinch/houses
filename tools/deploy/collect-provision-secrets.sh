#!/bin/bash
# tools/deploy/collect-provision-secrets.sh — print the values the
# `provision` workflow needs, straight from the CURRENT box. Run as root
# ON THE OLD BOX (your personal key, ad-hoc is fine here — this only
# READS; it never writes). Paste the output into GitHub repo Secrets.
#
# Usage:  sudo bash tools/deploy/collect-provision-secrets.sh
#
# Reads: the rclone remote config (restore/backup bucket creds), the age
# recipient + secret key (backup encryption/decryption), the Cloudflare
# tunnel token, and the deployed deploy-key pubkey. Values you cannot read
# from the box (OCI identifiers, your operator key) are listed as instr
# with a "SEE <dashboard>" line.
set -eu

say() { echo "=== $1"; }

say "RCLONE_CONFIG (bucket credentials — GitHub secret RCLONE_CONFIG)"
if [ -r /home/ubuntu/.config/rclone/rclone.conf ]; then
  echo "--- /home/ubuntu/.config/rclone/rclone.conf (remote '[houses]' body):"
  awk '/^\[/{remote=$0} remote ~ /\[houses\]/ || remote == "[houses]"{print}' \
    /home/ubuntu/.config/rclone/rclone.conf
elif [ -r /root/.config/rclone/rclone.conf ]; then
  cat /root/.config/rclone/rclone.conf
else
  echo "NOT FOUND — see the bucket provider's dashboard (access_key_id/secret)"
fi

say "AGE_RECIPIENT (public half — GitHub secret AGE_RECIPIENT)"
if [ -f /etc/houses-backup.env ]; then
  . /etc/houses-backup.env
  echo "RECIPIENT=$RECIPIENT"
elif [ -f /etc/systemd/system/houses-backup.service ]; then
  grep -o 'age1[a-z0-9]*' /etc/systemd/system/houses-backup.service /etc/systemd/system/houses-backup-push.service 2>/dev/null | sort -u
else
  echo "NOT FOUND — from your age keypair file (~/.config/rage/key.txt .pub, or where you generated it)"
fi

say "AGE_KEY (private half — GitHub secret AGE_KEY)"
echo "This value is NOT on the box by design. It lives wherever you created"
echo "the age keypair ('age-keygen -o key.txt'; the recipient prints next to it)."
echo "If you lost it the nightly backups are NOT restorable — address that"
echo "before rebuilding (generate a new keypair, re-encrypt the bucket)."

say "CF_TUNNEL_TOKEN (GitHub secret CF_TUNNEL_TOKEN)"
if [ -f /etc/cloudflared/token ]; then
  echo "TOKEN=$(cat /etc/cloudflared/token)"
else
  echo "NOT FOUND on box — dash.cloudflare.com → Zero Trust → Networks → Tunnels → your tunnel → token"
fi

say "DEPLOY_PUBKEY (GitHub secret DEPLOY_PUBKEY — the deploy key's PUBLIC half)"
grep '# houses-deploy-allowlist' -A1 /home/ubuntu/.ssh/authorized_keys 2>/dev/null \
  | awk '/ssh-ed25519|ssh-rsa/{print}' | tail -1 \
  || echo "NOT FOUND on box — the houses-deploy.pub file you generated for the deploy key"

say "OCI_* (GitHub secrets — NOT on the box; see cloud.oracle.com)"
echo "  OCI_USER_OCID:        My profile → OCID"
echo "  OCI_TENANCY_OCID:     Governance → Tenancy → OCID"
echo "  OCI_API_KEY:          My profile → API keys → Add (downloads .pem)"
echo "  OCI_FINGERPRINT:      shown on the same API keys page"
echo "  OCI_REGION:           region picker (e.g. eu-frankfurt-1)"
echo "  OCI_COMPARTMENT_OCID: Identity → Compartments"
echo "  OCI_SUBNET_OCID:      Networking → VCNs → VCN → Subnets"
echo "  OCI_IMAGE_OCID:       Compute → Images (Ubuntu 24.04)"

say "OPERATOR_PUBKEY (GitHub secret OPERATOR_PUBKEY — YOUR ssh pubkey)"
echo "  from your laptop:    cat ~/.ssh/id_ed25519.pub"

echo
echo "DONE — paste the printed values into GitHub → Settings → Secrets."