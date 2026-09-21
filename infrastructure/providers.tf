terraform {
  # Write-only-Passwörter benötigen Terraform 1.11 oder neuer.
  required_version = ">= 1.11, < 2.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
}
