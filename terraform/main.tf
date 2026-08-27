# Houses deployment box on Google Cloud — the permanent free tier.
#
# Brings up the whole box except the account itself:
#   - custom VPC + subnet + firewall (ingress 22 only — public traffic
#     comes through the Cloudflare tunnel, never to the origin port)
#   - e2-micro (1 vCPU burstable, 1 GB RAM) + 30 GB standard disk —
#     Google's ALWAYS-free VM allowance (no time limit, no idle reclaim,
#     no sleep). The app alone runs in ~100 MB; Chrome is NOT installed
#     (the Rightmove scraper lives on the LAN; the box enqueues scrape
#     jobs with retry — see houses/scrape_queue.py)
#   - static external IP (free while attached to a running VM)
#   - startup-script (cloud-init): deps, cloudflared, uv, the two repo
#     checkouts, systemd units — see user_data.sh + box-setup.sh
#
# No secrets live here: the .env (Google OAuth, session secret) is
# installed by the cutover SSH step as root-only /etc/houses.env. Only
# the public SSH key and a public repo URL appear in metadata/state.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
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

# SSH only — key-only auth. No 22/8765 ingress from the internet: public
# traffic enters via the Cloudflare tunnel (outbound connection), so the
# origin ports are never exposed.
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

# ── the box ─────────────────────────────────────────────────────────────

# Static external IP — free while attached to the running VM; stable for
# SSH and the tunnel hostname mapping.
resource "google_compute_address" "houses" {
  name   = "houses-static"
  region = var.region
}

resource "google_compute_instance" "houses" {
  name         = "houses"
  machine_type = "e2-micro"
  zone         = var.zone
  tags         = ["houses"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 30
      type  = "pd-standard"
    }
  }

  network_interface {
    network    = google_compute_network.houses.id
    subnetwork = google_compute_subnetwork.houses.id
    access_config {
      nat_ip = google_compute_address.houses.address
    }
  }

  metadata = {
    ssh-keys       = "ubuntu:${file(var.ssh_public_key_path)}"
    startup-script = templatefile("${path.module}/user_data.sh", { repo_url = var.repo_url, repo_branch = var.repo_branch })
  }

  depends_on = [google_compute_address.houses]
}
