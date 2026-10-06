# F10: Entra ergänzen, separat prüfen und erst danach die Laufzeiten umschalten.
variable "database_auth_phase" {
  description = "password (bisherige Anmeldung), prepare (Entra zusätzlich), entra (abgenommene Token-Anmeldung)."
  type        = string
  default     = "password"
  validation {
    condition     = contains(["password", "prepare", "entra"], var.database_auth_phase)
    error_message = "database_auth_phase muss password, prepare oder entra sein."
  }
  validation {
    condition     = var.database_auth_phase == "password" || (var.runtime_identity_phase == "split" && var.runtime_access_verified)
    error_message = "Entra setzt die abgenommene Trennung der Laufzeitrechte voraus."
  }
}

variable "database_entra_verified" {
  description = "Nach realer Token-/Rechte-/Erneuerungs- und Notzugangsprüfung aller drei Laufzeiten freigeben."
  type        = bool
  default     = false
  validation {
    condition     = var.database_auth_phase != "entra" || var.database_entra_verified
    error_message = "Die Token-Anmeldung muss vor entra separat in Azure abgenommen sein."
  }
}

variable "postgres_entra_admin_name" {
  description = "Anmeldename des persönlichen Entra-Administrators; nur lokale/CI-Konfiguration, keine Laufzeitidentität."
  type        = string
  default     = ""
  validation {
    condition     = var.database_auth_phase == "password" || trimspace(var.postgres_entra_admin_name) != ""
    error_message = "Für Entra muss der getrennte Administrator explizit benannt werden."
  }
  validation {
    # PostgreSQL kürzt Rollennamen auf 63 Zeichen, Azure speichert den Namen ebenso; ein
    # längerer Wert gilt sonst bei jedem Abgleich als geändert und erzwingt einen Ersatz.
    condition     = length(var.postgres_entra_admin_name) <= 63
    error_message = "Den Anmeldenamen auf 63 Zeichen gekürzt angeben, so wie Azure ihn speichert."
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
  # Host/DB/Rolle sind keine Geheimnisse. Token entstehen erst im Container.
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
    # Nach Aktivierung kontrolliert auf prepare zurückkehren, Entra nicht abbauen.
    prevent_destroy = true
    precondition {
      condition = (
        lower(var.owner_object_id) != lower(azurerm_user_assigned_identity.jobfinder.principal_id) &&
        lower(var.owner_object_id) != lower(try(azurerm_user_assigned_identity.review[0].principal_id, "")) &&
        lower(var.owner_object_id) != lower(var.local_docker_sp_object_id)
      )
      error_message = "Eine Laufzeitidentität darf nicht Entra-Datenbankadministrator werden."
    }
  }
}

output "entra_principals" {
  description = "Geplante Object-ID-Zuordnung für den separaten SQL-Einrichtungshelfer; keine Tokens."
  value = local.runtime_split ? {
    tenant_id = data.azurerm_client_config.current.tenant_id
    worker    = azurerm_user_assigned_identity.jobfinder.principal_id
    review    = azurerm_user_assigned_identity.review[0].principal_id
    hybrid    = var.local_docker_sp_object_id
  } : null
}

output "entra_database_urls" {
  description = "Passwortfreie DSNs für gezielte Proben nach Rollen-Einrichtung; keine Laufzeitaktivierung."
  value       = local.runtime_split ? local.entra_database_urls : null
}
