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

output "storage_account_name" {
  description = "Storage-Account für Bewerbungsdokumente, für Skripte und App-Konfiguration."
  value       = azurerm_storage_account.jobfinder.name
}

output "documents_container_name" {
  description = "Blob-Container innerhalb des Storage-Accounts, der die Dokumente enthält."
  value       = azurerm_storage_container.application_documents.name
}
