# Houses deployment box on Oracle Cloud Free Tier.
#
# Brings up the whole box except the account itself:
#   - VCN + subnet + security list (ingress 22 only — public traffic comes
#     through the Cloudflare tunnel, never to the origin port)
#   - VM.Standard.A1.Flex (4 OCPU / 24 GB — the Always-Free ARM shape)
#   - Reserved public IP (survives reboots; stable for SSH)
#   - cloud-init user_data: apt deps, Chrome, cloudflared, uv, the two
#     repo checkouts, systemd units — see user_data.sh + box-setup.sh
#
# No secrets live here: the .env (Google OAuth, session secret) is installed
# by the cutover SSH step as root-only /etc/houses.env. Only the public SSH
# key and a public repo URL appear in state/metadata.

terraform {
  required_version = ">= 1.5"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

provider "oci" {
  region           = var.region
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.api_key_fingerprint
  private_key_path = var.api_key_path
}

# ── identity ────────────────────────────────────────────────────────────

locals {
  compartment_ocid = var.compartment_ocid != "" ? var.compartment_ocid : var.tenancy_ocid
}


data "oci_identity_availability_domains" "ads" {
  compartment_id = local.compartment_ocid
}

data "oci_core_images" "ubuntu_arm64" {
  compartment_id   = local.compartment_ocid
  operating_system = "Canonical Ubuntu"
  shape            = "VM.Standard.A1.Flex"
  sort_by          = "TIMECREATED"
  sort_order       = "DESC"

  # Newest non-minimal 24.04 ARM image.  OCI names ARM images
  # "…-aarch64-<date>-0"; the regex skips the "-Minimal-" variants.
  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-24.04-aarch64-[0-9]"]
    regex  = true
  }
}

# ── networking ──────────────────────────────────────────────────────────

resource "oci_core_vcn" "houses" {
  compartment_id = local.compartment_ocid
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "houses-vcn"
  dns_label      = "houses"
}

resource "oci_core_internet_gateway" "houses" {
  compartment_id = local.compartment_ocid
  vcn_id         = oci_core_vcn.houses.id
  display_name   = "houses-igw"
}

resource "oci_core_route_table" "houses" {
  compartment_id = local.compartment_ocid
  vcn_id         = oci_core_vcn.houses.id
  display_name   = "houses-rt"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.houses.id
  }
}

resource "oci_core_subnet" "public" {
  compartment_id = local.compartment_ocid
  vcn_id         = oci_core_vcn.houses.id
  cidr_block     = "10.0.0.0/24"
  display_name   = "houses-public"
  dns_label      = "public"
  route_table_id = oci_core_route_table.houses.id
}

resource "oci_core_security_list" "houses" {
  compartment_id = local.compartment_ocid
  vcn_id         = oci_core_vcn.houses.id
  display_name   = "houses-security-list"

  # SSH only — key-only auth (password auth is off on Ubuntu cloud images).
  ingress_security_rules {
    protocol = "6" # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      min = 22
      max = 22
    }
    description = "SSH (key-only)"
  }

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    description = "outbound (tunnel, apt, git)"
  }
}

# ── the box ─────────────────────────────────────────────────────────────

resource "oci_core_instance" "houses" {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  compartment_id      = local.compartment_ocid
  shape               = "VM.Standard.A1.Flex"
  display_name        = "houses"

  shape_config {
    ocpus         = 4
    memory_in_gbs = 24
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu_arm64.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_size_gb
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
    user_data           = base64encode(templatefile("${path.module}/user_data.sh", { repo_url = var.repo_url }))
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = false
    display_name     = "houses-vnic"
  }
}

# Reserved public IP attached to the instance's primary private IP — stable
# across reboots (and instance replacement, since it is a separate resource).
data "oci_core_vnic_attachments" "houses" {
  compartment_id = local.compartment_ocid
  instance_id    = oci_core_instance.houses.id
}

data "oci_core_vnic" "houses" {
  vnic_id = data.oci_core_vnic_attachments.houses.vnic_attachments[0].vnic_id
}

data "oci_core_private_ips" "houses_primary" {
  subnet_id  = oci_core_subnet.public.id
  ip_address = data.oci_core_vnic.houses.private_ip_address
}

resource "oci_core_public_ip" "houses_reserved" {
  compartment_id = local.compartment_ocid
  lifetime       = "RESERVED"
  display_name   = "houses-reserved"
  private_ip_id  = data.oci_core_private_ips.houses_primary.private_ips[0].id
}
