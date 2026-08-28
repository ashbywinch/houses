variable "project" {
  description = "GCP project ID (the free-tier billing project)."
  type        = string
}

variable "region" {
  description = "Region for the e2-micro (free tier: us-west1, us-central1, us-east1)."
  type        = string
  default     = "us-west1"
}

variable "zone" {
  description = "Zone within the region (e.g. us-west1-a)."
  type        = string
  default     = "us-west1-a"
}

variable "ssh_public_key_path" {
  description = "ABSOLUTE path to the SSH public key for the box (terraform's file() does not expand ~ — /home/<you>/.ssh/oracle.pub)."
  type        = string
}

variable "main_host" {
  description = "Public hostname for the live app (Caddy + Google OAuth)."
  type        = string
  default     = "houses.blueumbrella.net"
}

variable "smoke_host" {
  description = "Public hostname for the standby/smoke app."
  type        = string
  default     = "houses-smoke.blueumbrella.net"
}

variable "repo_branch" {
  description = "Branch the box clones (the deploy tooling lives here until it merges to main)."
  type        = string
  default     = "deploy/oracle-free-tier"
}

variable "repo_url" {
  description = "Public clone URL for the repo — box-setup clones blue/green from it."
  type        = string
  default     = "https://github.com/ashbywinch/houses.git"
}
