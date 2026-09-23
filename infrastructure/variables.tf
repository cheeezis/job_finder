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

variable "review_fqdn" {
  description = "Vorhersehbarer Hostname der Review-Container-App; nicht aus der Ressource selbst ableitbar, da sie sich sonst auf sich selbst bezöge."
  type        = string
  default     = "jobfinder-review.ashyisland-3b6e9522.francecentral.azurecontainerapps.io"
}

variable "local_docker_sp_object_id" {
  description = "Objekt-ID des per az ad sp create angelegten Service Principals für den lokalen Docker-Worker-Lauf (StepStone/Remotely)."
  type        = string
  default     = "f270644a-c084-422f-a1db-ae500701dca0"
}

variable "alert_email" {
  description = "Empfängeradresse für Azure-Monitor-Alerts (fehlgeschlagene Finder-Läufe)."
  type        = string
  default     = "jannis.hauke@t-online.de"
}

variable "image_tag" {
  description = "Tag des jobfinder-Images nur für den Erstaufbau von Worker und Review-App. Danach setzt die CI/CD-Pipeline das Image; Terraform ignoriert Änderungen daran (lifecycle.ignore_changes)."
  type        = string
  default     = "azure-v9"
}

variable "owner_object_id" {
  description = "Objekt-ID des persönlichen Entra-ID-Kontos (Jannis). Bewusst fest statt über data.azurerm_client_config.current aufgelöst: sonst würde ein Terraform-Lauf durch die CI/CD-Pipeline die Dev-Berechtigungen und den Review-App-Login von der eigenen Person auf den GitHub-Actions-Principal umziehen."
  type        = string
  default     = "e417d473-3d02-48ee-9a4d-b2fab9bf84f1"
}

variable "github_actions_sp_object_id" {
  description = "Objekt-ID des per az ad sp create angelegten Service Principals für die GitHub-Actions-CI/CD-Pipeline (OIDC, kein gespeichertes Secret)."
  type        = string
  default     = "746f1edf-e808-4861-a7bb-af7c491ac17a"
}

variable "github_build_sp_object_id" {
  description = "Objekt-ID des Service Principals jobfinder-github-build: darf nur Images in die Registry hochladen (Build-Job auf main)."
  type        = string
  default     = "904edb79-936b-42a3-895f-2a271c9e8879"
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
