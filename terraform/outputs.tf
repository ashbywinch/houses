output "public_ip" {
  description = "Static external IP of the houses box — your SSH target."
  value       = google_compute_address.houses.address
}

output "instance_id" {
  description = "Name of the houses instance."
  value       = google_compute_instance.houses.id
}

output "ssh_command" {
  description = "Ready-made SSH command."
  value       = "ssh -i ~/.ssh/oracle ubuntu@${google_compute_address.houses.address}"
}
