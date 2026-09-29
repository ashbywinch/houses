# Houses deployment on Google Cloud — the TWO-INSTANCE data plane.
# See docs/anti-fragile-rollout-plan.md (Phase 2) and tools/deploy/provision.md.
#
# Four invariants, each stated so it is obvious why this cannot go wrong:
#
#  1. THE STATIC IP BELONGS TO THE FORWARDING RULE AND NOTHING ELSE. Instance
#     access configs are EPHEMERAL (control plane: SSH only). Traffic arrives at
#     the rule's target instance on 80/443 and is passed through to the box's
#     Caddy, so a rollout changes the rule's target — an IP is never attached to
#     or detached from an instance, and the 2026-09-24 "at most one access
#     config" failure class cannot occur. The address keeps prevent_destroy
#     (it never changes); instances cannot, because after a flip the protected
#     resource has moved.
#  2. BOXES HOLD NO SECRETS. Each instance runs as the houses-box-deploy SA and
#     every gsutil/gcloud call uses the metadata-server identity. Metadata
#     carries only PUBLIC keys (ssh-keys, the deploy pubkey) and PUBLIC vars
#     (PROVISION_REF, PROVISION_ARTIFACT). No key file exists on a box and none
#     can appear in state.
#  3. THE RULE'S TARGET IS THE ONLY "WHO IS LIVE" FACT. The two instances are
#     fixed resources whose roles rotate with the target, so Terraform IGNORES
#     the rule target (CI flips it with `set-target`) and a later `apply` can
#     never silently move traffic back.
#  4. EXACTLY ONE HUMAN DECISION PER ROLLOUT — the cutover approval in the
#     Release workflow's production environment. Everything else is automated.

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Operator-created once (chicken-and-egg — Terraform cannot create its own
  # state bucket):
  #   gsutil mb -l us-west1 -b on gs://houses-tfstate
  # then `terraform init -migrate-state` once to move the local state into it.
  backend "gcs" {
    bucket = "houses-tfstate"
    prefix = "houses"
  }
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

locals {
  # The two fixed instances. Roles (owner/standby) rotate with the rule target;
  # the names never change.
  instances = {
    house   = "houses"
    standby = "houses-standby"
  }

  box_service_account = google_service_account.box_deploy.email

  # Which instance the rule points at on first apply / adoption. Afterwards CI
  # owns it (invariant 3): the rule target is ignored by Terraform and flipped
  # with `gcloud compute forwarding-rules set-target`.
  live_key = var.live_instance == "houses" ? "house" : "standby"

  # The startup-script is rendered ONCE here, from the repo's bootstrap, so
  # "what a fresh box becomes" has exactly one definition.
  startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    provision_ref      = var.provision_ref
    provision_artifact = var.provision_artifact
    operator_pubkey    = file(var.operator_ssh_public_key_path)
    deploy_pubkey      = var.deploy_pubkey
    project            = var.project
    seed_object        = var.seed_object
    env_object         = var.env_object
    main_host          = var.main_host
    bootstrap          = file("${path.module}/../tools/deploy/box-bootstrap.sh")
  })
}

# ── networking ──────────────────────────────────────────────────────────

resource "google_compute_network" "houses" {
  name                    = "houses-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "houses" {
  name          = "houses-subnet"
  network       = google_compute_network.houses.id
  region        = var.region
  ip_cidr_range = "10.0.0.0/24"
}

# SSH is the control plane (key-only, from anywhere); 80/443 are the L4 rule's
# passthrough into the box's Caddy. The app port (8765) is never exposed.
resource "google_compute_firewall" "ssh" {
  name          = "houses-allow-ssh"
  network       = google_compute_network.houses.name
  target_tags   = ["houses"]
  source_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "https" {
  name          = "houses-allow-https"
  network       = google_compute_network.houses.name
  target_tags   = ["houses"]
  source_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

# ── the data plane ──────────────────────────────────────────────────────

# The static origin address: the rule's address, never an instance's.
resource "google_compute_address" "houses" {
  name   = "houses-static"
  region = var.region

  lifecycle {
    # Never recreate: the DNS A records and every browser's cached TLS state
    # point at this address.
    prevent_destroy = true
  }
}

# One target instance per box: the L4 rule's `target` names one of these, and
# that is the whole of "who is live".
resource "google_compute_target_instance" "box" {
  for_each   = local.instances
  name       = "${each.value}-target"
  instance   = google_compute_instance.box[each.key].self_link
  zone       = var.zone
  nat_policy = "NO_NAT" # passthrough: the box answers the client's own connection
}

# Two rules, one per port — a target-instance rule takes a single port or range.
# They are ALWAYS flipped together; `switch.sh --diagnose` prints both targets
# and the cutover refuses to flip if they disagree.
resource "google_compute_forwarding_rule" "http" {
  name                  = "houses-l4-http"
  region                = var.region
  target                = google_compute_target_instance.box[local.live_key].self_link
  ip_address            = google_compute_address.houses.address
  port_range            = "80"
  load_balancing_scheme = ""

  lifecycle {
    # CI owns the target (invariant 3): an apply must never reset the rollout's
    # traffic decision.
    ignore_changes = [target]
  }
}

resource "google_compute_forwarding_rule" "https" {
  name                  = "houses-l4-https"
  region                = var.region
  target                = google_compute_target_instance.box[local.live_key].self_link
  ip_address            = google_compute_address.houses.address
  port_range            = "443"
  load_balancing_scheme = ""

  lifecycle {
    ignore_changes = [target]
  }
}

# ── the instances ───────────────────────────────────────────────────────

# The live box already exists in the operator's state as
# `google_compute_instance.houses`. Declaring the rename here is what stops an
# adoption apply from planning "destroy the old instance, create a new one" —
# which would take the production database with it. Terraform state operations
# (`moved`) are the ONLY safe way to rename a resource that exists in the world.
moved {
  from = google_compute_instance.houses
  to   = google_compute_instance.box["house"]
}

moved {
  from = google_compute_firewall.web
  to   = google_compute_firewall.https
}


# The instance identity every box-side gsutil/gcloud call uses — no key file
# anywhere, and nothing secret in metadata. The scopes are the API surface, the
# IAM roles below are the permissions.
resource "google_service_account" "box_deploy" {
  account_id   = "houses-box-deploy"
  display_name = "Houses box deploy (artifacts + seed reads)"
}

# ── the base machine image ──────────────────────────────────────────────
# A rollout builds a FRESH box, so the OS layer is baked once here instead of
# being apt-installed on every rollout: faster, and independent of the apt mirrors
# and the Caddy repo at rollout time. The bake script has no secrets and no app —
# the artifact supplies code, the seed supplies data.
#
# Re-bake: the workflow's `action=bake` (terraform apply -target/-replace
# google_compute_instance.base_builder, then the image is recreated from its
# finished disk). The boxes pick the new image up when they are next rebuilt.
resource "google_compute_instance" "base_builder" {
  name         = "houses-base-builder"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["houses"]

  boot_disk {
    initialize_params {
      image = var.stock_image
      size  = var.boot_disk_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    network    = google_compute_network.houses.id
    subnetwork = google_compute_subnetwork.houses.id
    access_config {} # the bake needs the internet (apt, the Caddy repo)
  }

  metadata = {
    startup-script = file("${path.module}/../tools/deploy/base-image.sh")
  }

  # The bake script powers the instance off when it has FINISHED; terraform must
  # not stop it on creation or the bake would be truncated mid-apt. The bake
  # workflow waits for the stopped state before creating the image from the disk.
}

# The IMAGE itself is created by the bake workflow, not here: an image must be
# taken from a disk that is finished being written, and terraform cannot observe
# the bake's completion. `tools/deploy/base-image.sh` powers the box off when it
# is done; the workflow waits for that state and then images the disk. The boxes
# reference the image BY NAME (var.base_image), so there is no provider-side
# ordering to get wrong.
resource "google_compute_instance" "box" {
  for_each     = local.instances
  name         = each.value
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["houses"]

  boot_disk {
    initialize_params {
      image = var.base_image
      size  = var.boot_disk_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    network    = google_compute_network.houses.id
    subnetwork = google_compute_subnetwork.houses.id
    # EPHEMERAL — SSH only. The static address belongs to the forwarding rule
    # (invariant 1).
    access_config {}
  }

  service_account {
    email  = local.box_service_account
    scopes = ["cloud-platform"]
  }

  metadata = {
    ssh-keys           = "ubuntu:${file(var.operator_ssh_public_key_path)}"
    PROVISION_REF      = var.provision_ref
    PROVISION_ARTIFACT = var.provision_artifact
    startup-script     = local.startup_script
  }

  # A box is NEVER modified in place by terraform — only replaced by the rollout
  # (the workflow's `-replace`), so the disk that holds the live database must
  # never be re-created because a variable changed. The base image therefore
  # takes effect on the next rebuild, not on an apply.
  lifecycle {
    ignore_changes = [boot_disk]
  }

  # Assigning the box service account (the live box has the default one) is an
  # in-place update that needs the guest stopped; terraform may do that briefly.
  allow_stopping_for_update = true
}

# ── buckets ─────────────────────────────────────────────────────────────

# Content-addressed build artifacts: gs://houses-artifacts/<sha256>.tar.gz.
# The box fetches by its OWN identity (invariant 2).
resource "google_storage_bucket" "artifacts" {
  name                        = var.artifact_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  # The cutover archives each clean PRE-MIGRATION snapshot here
  # (snapshots/<timestamp>.db, written by CI). Retention is bounded: 90 days is
  # far past any recovery horizon — the seed is the durable recovery artifact,
  # snapshots are the recent clean copies the process must always have made.
  lifecycle_rule {
    action { type = "Delete" }
    condition {
      age            = 90
      matches_prefix = ["snapshots/"]
    }
  }
}

resource "google_storage_bucket_iam_member" "box_reads_artifacts" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.box_service_account}"
}

# The seed bucket is operator-owned (a human validates the seed via
# tools/deploy/seed-box.sh); Terraform only grants the box read.
data "google_storage_bucket" "seed" {
  name = var.seed_bucket
}

resource "google_storage_bucket_iam_member" "box_reads_seed" {
  bucket = data.google_storage_bucket.seed.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.box_service_account}"
}
