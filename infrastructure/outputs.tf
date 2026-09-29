output "postgres_server_name" {
  description = "Servername für Azure-CLI-Abfragen und Start/Stop."
  value       = azurerm_postgresql_flexible_server.jobfinder.name
}

output "postgres_host" {
  description = "DNS-Name für die spätere TLS-Verbindung zur Cloud-Datenbank."
  value       = azurerm_postgresql_flexible_server.jobfinder.fqdn
}

output "postgres_database" {
  description = "Name der Anwendungsdatenbank innerhalb des Servers."
  value       = azurerm_postgresql_flexible_server_database.jobfinder.name
}
