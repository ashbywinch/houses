variable "region" {
  description = "OCI region (e.g. eu-frankfurt-1, us-ashburn-1). Pick one with A1 capacity."
  type        = string
}

variable "tenancy_ocid" {
  description = "Tenancy OCID — Console → your profile → Tenancy."
  type        = string
  sensitive   = true
}

variable "user_ocid" {
  description = "User OCID — Console → your profile → User settings."
  type        = string
  sensitive   = true
}

variable "compartment_ocid" {
  description = "Compartment OCID. Defaults to the tenancy (root) compartment."
  type        = string
  sensitive   = true
  default     = ""
}

variable "api_key_fingerprint" {
  description = "Fingerprint of the API signing key — shown when you upload it in the console."
  type        = string
  sensitive   = true
}

variable "api_key_path" {
  description = "Path to the OCI API signing key PRIVATE key (e.g. ~/.oci/oci_api_key.pem)."
  type        = string
  default     = "~/.oci/oci_api_key.pem"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key for the box (e.g. ~/.ssh/oracle.pub)."
  type        = string
  default     = "~/.ssh/oracle.pub"
}

variable "repo_url" {
  description = "Public clone URL for the repo — box-setup clones blue/green from it."
  type        = string
  default     = "https://github.com/ashbywinch/houses.git"
}

variable "boot_volume_size_gb" {
  description = "Boot volume size (GB). Free tier includes 200 GB total block storage."
  type        = number
  default     = 200
}
