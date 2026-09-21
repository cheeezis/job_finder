terraform {
  # Write-only-Passwörter benötigen Terraform 1.11 oder neuer.
  required_version = ">= 1.11, < 2.0"

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
