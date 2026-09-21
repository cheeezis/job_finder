# Phase 7: Der verwaltete Server übernimmt Betrieb, Updates und Serverbackups.
# Der Suffix macht den DNS-Namen subscriptionspezifisch und reproduzierbar.
resource "azurerm_postgresql_flexible_server" "jobfinder" {
  name                = "psql-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  version             = "18"

  # Kleine Burstable-Instanz für den Einstieg: 1 vCPU, 2 GiB RAM, 32 GiB SSD.
  # Der Server verursacht auch zwischen Finder-Läufen laufende Kosten.
  sku_name          = "B_Standard_B1ms"
  storage_mb        = 32768
  storage_tier      = "P4"
  auto_grow_enabled = false

  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true

  # Nur für Einrichtung/Verwaltung. Ein eigener Anwendungsbenutzer folgt vor
  # der Cloud-Anbindung des Finders. Das lokale Secret bleibt außerhalb von Git.
  administrator_login               = "jobfinder_admin"
  administrator_password_wo         = var.postgres_admin_password
  administrator_password_wo_version = 1

  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = false
  }

  # Azure wählt die verfügbare Zone bei der Erstellung. Diese Wahl beibehalten,
  # statt beim nächsten Plan eine Änderung auf einen leeren Wert zu verlangen.
  lifecycle {
    ignore_changes = [zone]
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Öffentlicher Endpunkt bedeutet nicht Zugriff von überall: Azure erlaubt hier
# nur die einzelne angegebene IP. Bei einem IP-Wechsel muss sie aktualisiert werden.
# Die pauschale Freigabe für alle Azure-Dienste (0.0.0.0) wird nicht verwendet.
resource "azurerm_postgresql_flexible_server_firewall_rule" "local_review" {
  name             = "local-review"
  server_id        = azurerm_postgresql_flexible_server.jobfinder.id
  start_ip_address = var.postgres_client_ipv4
  end_ip_address   = var.postgres_client_ipv4
}

# Erzwingt verschlüsselte Verbindungen. Der Client prüft zusätzlich Zertifikat
# und Hostnamen mit sslmode=verify-full, sobald wir ihn an Azure anbinden.
resource "azurerm_postgresql_flexible_server_configuration" "require_tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  value     = "on"
}

# Der Server ist der verwaltete Dienst; darin liegt die eigentliche Jobfinder-DB.
resource "azurerm_postgresql_flexible_server_database" "jobfinder" {
  name      = "jobfinder"
  server_id = azurerm_postgresql_flexible_server.jobfinder.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
