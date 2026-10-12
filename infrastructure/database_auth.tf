# F10: the runtimes sign in to PostgreSQL with Entra tokens of their own identities.
variable "postgres_entra_admin_name" {
  description = "Sign-in name of the personal Entra administrator; only local/CI configuration, no runtime identity."
  type        = string
  validation {
    condition     = trimspace(var.postgres_entra_admin_name) != ""
    error_message = "The separate Entra administrator must be named explicitly."
  }
  validation {
    # PostgreSQL shortens role names to 63 characters, and Azure stores the name the same
    # way; a longer value counts as changed in every refresh and forces a replacement.
    condition     = length(var.postgres_entra_admin_name) <= 63
    error_message = "Give the sign-in name shortened to 63 characters, as Azure stores it."
  }
}

locals {
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
  server_name         = azurerm_postgresql_flexible_server.jobfinder.name
  resource_group_name = azurerm_resource_group.jobfinder.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = var.owner_object_id
  principal_name      = var.postgres_entra_admin_name
  principal_type      = "User"

  lifecycle {
    # Worker, review and hybrid run only with Entra.
    prevent_destroy = true
    precondition {
      condition = (
        lower(var.owner_object_id) != lower(azurerm_user_assigned_identity.jobfinder.principal_id) &&
        lower(var.owner_object_id) != lower(azurerm_user_assigned_identity.review.principal_id) &&
        lower(var.owner_object_id) != lower(var.local_docker_sp_object_id)
      )
      error_message = "A runtime identity must not become the Entra database administrator."
    }
  }
}

moved {
  from = azurerm_postgresql_flexible_server_active_directory_administrator.owner[0]
  to   = azurerm_postgresql_flexible_server_active_directory_administrator.owner
}

output "entra_principals" {
  description = "Object IDs for the separate SQL setup helper; no tokens."
  value = {
    tenant_id = data.azurerm_client_config.current.tenant_id
    worker    = azurerm_user_assigned_identity.jobfinder.principal_id
    review    = azurerm_user_assigned_identity.review.principal_id
    hybrid    = var.local_docker_sp_object_id
  }
}

output "entra_database_urls" {
  description = "Password-free DSNs of the three runtime roles."
  value       = local.entra_database_urls
}
