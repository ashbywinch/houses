output "live_target" {
  description = "The L4 rule's current target instance — the ONE 'who is live' fact. CI flips it; Terraform ignores changes to it."
  value       = google_compute_forwarding_rule.https.target
}

output "address" {
  description = "The static origin address (the rule's address — never an instance's)."
  value       = google_compute_address.houses.address
}

output "ssh_house" {
  description = "SSH to the box named houses (its EPHEMERAL address; the static one is the rule's)."
  value       = "ssh -i ~/.ssh/houses_operator ubuntu@${google_compute_instance.box["house"].network_interface[0].access_config[0].nat_ip}"
}

output "ssh_standby" {
  description = "SSH to the box named houses-standby (its EPHEMERAL address)."
  value       = "ssh -i ~/.ssh/houses_operator ubuntu@${google_compute_instance.box["standby"].network_interface[0].access_config[0].nat_ip}"
}

output "box_service_account" {
  description = "The instance identity every box-side gsutil/gcloud call uses (no key files)."
  value       = google_service_account.box_deploy.email
}

output "artifact_prefix" {
  description = "Where artifacts live and what a fresh box installs."
  value       = "gs://${google_storage_bucket.artifacts.name}/<sha256>.tar.gz"
}

output "base_image" {
  description = "The baked OS image every box is created from (rebuild it with the workflow's bake action)."
  value       = var.base_image
}
