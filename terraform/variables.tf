variable "project" {
  description = "GCP project ID (the free-tier billing project)."
  type        = string
}

variable "region" {
  description = "Region for the boxes and the L4 rule (free tier: us-west1, us-central1, us-east1)."
  type        = string
  default     = "us-west1"
}

variable "zone" {
  description = "Zone within the region (e.g. us-west1-a)."
  type        = string
  default     = "us-west1-a"
}


variable "base_image" {
  description = "The baked OS image every box is created from. Created by the Release workflow's bake action (not by terraform — an image must be taken from a finished disk); the boxes reference it by name, and the bootstrap's own apt install remains the fallback if it does not exist yet."
  type        = string
  default     = "houses-base"
}

variable "stock_image" {
  description = "The upstream image the BASE IMAGE is baked from. The boxes are created from the baked image, not from this — see google_compute_image.base."
  type        = string
  default     = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
}

variable "machine_type" {
  description = "Instance type. e2-micro is Google's always-free allowance (1 vCPU burstable, 1 GB RAM)."
  type        = string
  default     = "e2-micro"
}

variable "boot_disk_gb" {
  description = "Boot disk size in GB (30 fits the free tier's 30 GB always-free disk)."
  type        = number
  default     = 30
}

variable "operator_ssh_public_key_path" {
  description = "ABSOLUTE path to the operator's SSH PUBLIC key (terraform's file() does not expand ~ — /home/<you>/.ssh/houses_operator.pub). This is the human admin key: shell + sudo on either box."
  type        = string
}

variable "deploy_pubkey" {
  description = "The CI deploy key's PUBLIC half. It is installed with a forced-command allowlist (never a plain login), so it must NOT go into the ssh-keys metadata."
  type        = string
}

variable "provision_ref" {
  description = "Git ref the boxes report as provisioned (the ARTIFACT marker carries the artifact hash; this is the human-facing label)."

  type = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.provision_ref))
    error_message = "A git ref may contain only [A-Za-z0-9._/-]."
  }
}

variable "provision_artifact" {
  description = "The artifact object a fresh box installs: gs://<bucket>/<sha256>.tar.gz. Public metadata (no secret) — the box fetches it with its own identity. Empty is allowed so a bake/adoption apply need not invent one; a box created without it fails loudly at boot."

  type    = string
  default = ""

  validation {
    condition     = var.provision_artifact == "" || can(regex("^gs://[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+\\.tar\\.gz$", var.provision_artifact))
    error_message = "The artifact must be gs://<bucket>/<sha256>.tar.gz (or empty)."
  }
}

variable "live_instance" {
  description = "Which instance the L4 rule targets on first apply/adoption: 'houses' or 'houses-standby'. Afterwards CI owns the target (the rule ignores changes to it), so this value only matters for the adoption run."
  type        = string
  default     = "houses"

  validation {
    condition     = contains(["houses", "houses-standby"], var.live_instance)
    error_message = "live_instance must be 'houses' or 'houses-standby'."
  }
}

variable "main_host" {
  description = "Public hostname of the live app (Caddy + Google OAuth)."
  type        = string
  default     = "houses.blueumbrella.net"
}

variable "seed_bucket" {
  description = "The human-validated seed bucket (created by an operator; terraform only grants the box read)."
  type        = string
  default     = "houses-seed"
}

variable "seed_object" {
  description = "The seed object a fresh box restores. Deliberately not auto-refreshed — tools/deploy/seed-box.sh is the human-validated refresh."
  type        = string
  default     = "gs://houses-seed/latest.db"
}

variable "env_object" {
  description = "The private object holding the app's root-only /etc/houses.env (session secret, API keys). Read by the box with its own identity."
  type        = string
  default     = "gs://houses-seed/houses.env"
}

variable "artifact_bucket" {
  description = "Bucket for content-addressed build artifacts (gs://<bucket>/<sha256>.tar.gz)."
  type        = string
  default     = "houses-artifacts"
}
