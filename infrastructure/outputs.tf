output "postgres_server_name" {
  description = "Server name for Azure CLI queries and start/stop."
  value       = azurerm_postgresql_flexible_server.jobfinder.name
}

output "postgres_host" {
  description = "DNS name for the TLS connection to the cloud database."
  value       = azurerm_postgresql_flexible_server.jobfinder.fqdn
}

output "postgres_database" {
  description = "Name of the application database within the server."
  value       = azurerm_postgresql_flexible_server_database.jobfinder.name
}
