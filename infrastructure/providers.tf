terraform {
  # Write-only passwords need Terraform 1.11 or newer.
  required_version = ">= 1.11, < 2.0"

  # The state lives in the same storage account as the application documents,
  # but in a container of its own. Enables CI access and locking through
  # blob leases; before, it existed only as a local, unshared file.
  # use_azuread_auth enforces RBAC access (Storage Blob Data Contributor)
  # instead of an automatically resolved storage account key: the
  # GitHub Actions principal deliberately has no permission to list keys,
  # only the more narrowly scoped blob role on the tfstate container.
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
    # Only for the one resource azurerm does not (yet) cover: the
    # auth configuration of the review container app (Easy Auth).
    azapi = {
      source  = "azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azurerm" {
  features {
    # No data plane queries for queues and static websites, which the project
    # does not use; they used to go through the account key.
    storage {
      data_plane_available = false
    }
  }

  subscription_id = var.subscription_id
  # Storage access through Entra ID instead of account keys; the
  # storage account no longer allows keys at all (storage.tf).
  storage_use_azuread = true
}

provider "azapi" {
  subscription_id = var.subscription_id
}
