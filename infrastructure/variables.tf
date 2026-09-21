variable "subscription_id" {
  description = "Azure-Subscription, in der die Jobfinder-Infrastruktur erstellt wird."
  type        = string
}

variable "location" {
  description = "Azure-Region für die Jobfinder-Ressourcen."
  type        = string
  default     = "francecentral"
}

variable "postgres_admin_password" {
  description = "Lokales Admin-Secret; wird weder im Plan noch im State gespeichert."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "review_aad_client_id" {
  description = "App-ID der per az ad app create angelegten Entra-ID-Registrierung für Easy Auth."
  type        = string
  default     = "323cccd5-c4d1-4380-a5c7-818cc20ffb0b"
}

variable "postgres_client_ipv4" {
  description = "Aktuelle öffentliche IPv4 des Rechners für den gezielten Datenbankzugriff."
  type        = string

  validation {
    condition = (
      can(cidrnetmask("${var.postgres_client_ipv4}/32")) &&
      var.postgres_client_ipv4 != "0.0.0.0" &&
      var.postgres_client_ipv4 != "255.255.255.255"
    )
    error_message = "Eine einzelne IPv4-Adresse angeben; 0.0.0.0 ist nicht erlaubt."
  }
}
