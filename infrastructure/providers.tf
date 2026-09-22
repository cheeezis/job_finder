terraform {
  # Write-only-Passwörter benötigen Terraform 1.11 oder neuer.
  required_version = ">= 1.11, < 2.0"

  # State liegt im selben Storage-Account wie die Bewerbungsdokumente, aber in
  # einem eigenen Container. Ermöglicht CI-Zugriff (Phase 10) und Locking über
  # Blob-Leases; vorher nur als lokale, ungeteilte Datei vorhanden.
  # use_azuread_auth erzwingt RBAC-Zugriff (Storage Blob Data Contributor)
  # statt eines automatisch aufgelösten Storage-Account-Keys: Der
  # GitHub-Actions-Principal hat bewusst keine Berechtigung, Keys aufzulisten,
  # nur die enger gescopte Blob-Rolle auf den tfstate-Container.
  backend "azurerm" {
    resource_group_name  = "rg-jobfinder"
    storage_account_name = "stjobfindere64bfdce"
    container_name       = "tfstate"
    key                  = "jobfinder.tfstate"
    use_azuread_auth     = true
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    # Nur für die eine Ressource, die azurerm (noch) nicht abbildet: die
    # Auth-Konfiguration der Review-Container-App (Easy Auth).
    azapi = {
      source  = "azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
}

provider "azapi" {
  subscription_id = var.subscription_id
}
