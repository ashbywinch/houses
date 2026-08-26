output "public_ip" {
  description = "Reserved public IP of the houses box — your SSH target."
  value       = oci_core_public_ip.houses_reserved.ip_address
}

output "instance_id" {
  description = "OCID of the houses instance."
  value       = oci_core_instance.houses.id
}

output "ssh_command" {
  description = "Ready-made SSH command (after you add ~/.ssh/oracle)."
  value       = "ssh -i ~/.ssh/oracle ubuntu@${oci_core_public_ip.houses_reserved.ip_address}"
}
