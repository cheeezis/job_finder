# F10: add Entra, check it separately and only then switch the runtimes.
variable "database_auth_phase" {
  description = "password (earlier sign-in), prepare (Entra in addition), entra (accepted token sign-in)."
  type        = string
  default     = "password"
  validation {
    condition     = contains(["password", "prepare", "entra"], var.database_auth_phase)
    error_message = "database_auth_phase must be password, prepare or entra."
  }
  validation {
    condition     = var.database_auth_phase == "password" || (var.runtime_identity_phase == "split" && var.runtime_access_verified)
    error_message = "Entra requires the accepted separation of runtime rights."
  }
}

variable "database_entra_verified" {
  description = "Approve after a real check of token, rights, renewal and emergency access for all three runtimes."
  type        = bool
  default     = false
  validation {
    condition     = var.database_auth_phase != "entra" || var.database_entra_verified
    error_message = "The token sign-in must be accepted separately in Azure before entra."
  }
}

variable "postgres_entra_admin_name" {
  description = "Sign-in name of the personal Entra administrator; only local/CI configuration, no runtime identity."
  type        = string
  default     = ""
  validation {
    condition     = var.database_auth_phase == "password" || trimspace(var.postgres_entra_admin_name) != ""
    error_message = "For Entra the separate administrator must be named explicitly."
  }
  validation {
    # PostgreSQL shortens role names to 63 characters, and Azure stores the name the same
    # way; a longer value counts as changed in every refresh and forces a replacement.
    condition     = length(var.postgres_entra_admin_name) <= 63
    error_message = "Give the sign-in name shortened to 63 characters, as Azure stores it."
  }
}

locals {
  database_entra_prepared = var.database_auth_phase != "password"
  database_entra_active   = var.database_auth_phase == "entra" && var.database_entra_verified
  entra_database_roles = {
    worker = "jobfinder_worker_entra"
    review = "jobfinder_review_entra"
    hybrid = "jobfinder_hybrid_entra"
  }
  # Host/database/role are no secrets. Tokens are created only in the container.
  entra_database_urls = {
    for component, role in local.entra_database_roles : component => "postgresql://${role}@${azurerm_postgresql_flexible_server.jobfinder.fqdn}:5432/${azurerm_postgresql_flexible_server_database.jobfinder.name}?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
  }
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "owner" {
  count               = local.database_entra_prepared ? 1 : 0
  server_name         = azurerm_postgresql_flexible_server.jobfinder.name
  resource_group_name = azurerm_resource_group.jobfinder.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = var.owner_object_id
  principal_name      = var.postgres_entra_admin_name
  principal_type      = "User"

  lifecycle {
    # After an activation return to prepare in a controlled way; do not remove Entra.
    prevent_destroy = true
    precondition {
      condition = (
        lower(var.owner_object_id) != lower(azurerm_user_assigned_identity.jobfinder.principal_id) &&
        lower(var.owner_object_id) != lower(try(azurerm_user_assigned_identity.review[0].principal_id, "")) &&
        lower(var.owner_object_id) != lower(var.local_docker_sp_object_id)
      )
      error_message = "A runtime identity must not become the Entra database administrator."
    }
  }
}

output "entra_principals" {
  description = "Planned object ID assignment for the separate SQL setup helper; no tokens."
  value = local.runtime_split ? {
    tenant_id = data.azurerm_client_config.current.tenant_id
    worker    = azurerm_user_assigned_identity.jobfinder.principal_id
    review    = azurerm_user_assigned_identity.review[0].principal_id
    hybrid    = var.local_docker_sp_object_id
  } : null
}

output "entra_database_urls" {
  description = "Password-free DSNs for targeted checks after the role setup; no runtime activation."
  value       = local.runtime_split ? local.entra_database_urls : null
}
