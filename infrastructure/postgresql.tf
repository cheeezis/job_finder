# The managed server takes care of operation, updates and server backups.
# The suffix makes the DNS name subscription-specific and reproducible.
resource "azurerm_postgresql_flexible_server" "jobfinder" {
  name                = "psql-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  version             = "18"

  # Small burstable instance to start with: 1 vCPU, 2 GiB RAM, 32 GiB SSD.
  # The server causes running costs between finder runs as well.
  sku_name          = "B_Standard_B1ms"
  storage_mb        = 32768
  storage_tier      = "P4"
  auto_grow_enabled = false

  # Shared recovery window with blob/container soft delete.
  # It limits old backup states, not the stored applications.
  backup_retention_days         = 14
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true

  # Only for setup/administration; runtimes use their own restricted
  # roles. This administrative way back stays during Entra.
  administrator_login               = "jobfinder_admin"
  administrator_password_wo         = var.postgres_admin_password
  administrator_password_wo_version = 1

  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = local.database_entra_prepared
    tenant_id                     = local.database_entra_prepared ? data.azurerm_client_config.current.tenant_id : null
  }

  # Azure picks the available zone at creation. Keep this choice instead
  # of asking for a change to an empty value in the next plan.
  # Terraform aborts a plan that would delete or recreate the server
  # (docs/operations.md).
  lifecycle {
    ignore_changes  = [zone]
    prevent_destroy = true
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# A public endpoint does not mean access from everywhere: this rule allows
# only a single IP. The home IP changes often; scripts/run_local_hybrid.py
# points the rule at the current address before every run (or with --allow-ip).
# Terraform creates the rule and leaves the address to the script afterwards.
resource "azurerm_postgresql_flexible_server_firewall_rule" "local_review" {
  name             = "local-review"
  server_id        = azurerm_postgresql_flexible_server.jobfinder.id
  start_ip_address = var.postgres_client_ipv4
  end_ip_address   = var.postgres_client_ipv4

  lifecycle {
    ignore_changes = [start_ip_address, end_ip_address]
  }
}

# The worker runs as a Container Apps job without a fixed outbound IP. Private
# access would need a newly created, VNet-integrated environment with a private
# endpoint and is deliberately not implemented for this scope; the trade-off is
# in docs/operations.md. Start/end 0.0.0.0 is Azure's special value for "any
# Azure service", not only this subscription. TLS (verify-full) and
# the separate roles with password/token remain the access barrier.
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.jobfinder.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Enforces encrypted connections. The client additionally verifies the
# certificate and host name with sslmode=verify-full.
resource "azurerm_postgresql_flexible_server_configuration" "require_tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  value     = "on"
}

# At least TLS 1.2, sign-in attempts and checkpoints in the server log: that is
# already Azure's default today. Written down, it holds even after a changed
# default, and the scans see it. All three apply without a restart. The
# server log only becomes readable with a log download or a diagnostic
# setting; both are currently off.
resource "azurerm_postgresql_flexible_server_configuration" "min_tls_version" {
  name      = "ssl_min_protocol_version"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  value     = "TLSv1.2"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_connections" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_checkpoints" {
  name      = "log_checkpoints"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  value     = "on"
}

# The server is the managed service; the actual job finder database lives in it.
resource "azurerm_postgresql_flexible_server_database" "jobfinder" {
  name      = "jobfinder"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    prevent_destroy = true
  }
}

# Delete lock for the server with the review decisions: the server backups
# help against wrongly changed data, but after the server is deleted only
# briefly and awkwardly. IP changes to the firewall rules are changes
# and keep working. Created and removed only locally, as in storage.tf.
resource "azurerm_management_lock" "postgres" {
  name       = "no-delete"
  scope      = azurerm_postgresql_flexible_server.jobfinder.id
  lock_level = "CanNotDelete"
  notes      = "Review-Entscheidungen und Bewerbungsverlauf. Entfernen nur bewusst und lokal."
}
