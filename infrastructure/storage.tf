# Permanent store for application documents, separate from the database.
# The name must be globally unique; same hash pattern as for the Postgres server.
resource "azurerm_storage_account" "jobfinder" {
  name                = "stjobfinder${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  # Lowest redundancy level to start with; enough for this learning project.
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  # No access through the account keys: whoever can query them (for example
  # with Contributor) still gets no data access that way. All access
  # runs through Entra ID and the narrowly scoped roles below.
  shared_access_key_enabled = false

  # The account holds the only cloud copies of the application documents and the
  # Terraform state. Overwritten blobs remain as versions,
  # deleted blobs and containers can be restored for 14 days.
  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 14
    }

    container_delete_retention_policy {
      days = 14
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # In addition to the delete lock below: Terraform aborts a plan that would
  # delete or recreate the account already before the apply.
  lifecycle {
    prevent_destroy = true
  }
}

# Without cleanup, every Terraform apply adds a state version.
# Clean up only state versions: a later database restore can need old
# application documents that are still referenced. Their versions must
# therefore not disappear across the board by their creation age.
resource "azurerm_storage_management_policy" "jobfinder" {
  storage_account_id = azurerm_storage_account.jobfinder.id

  rule {
    name    = "delete-old-versions"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["tfstate/"]
    }

    actions {
      version {
        delete_after_days_since_creation = 30
      }
    }
  }
}

# Delete lock for the whole account: soft delete rescues only single blobs and
# containers, not a deleted account with its documents and Terraform state.
# Changing stays allowed, deleting does not - also not for containers and
# role assignments below it. Only an owner may create or remove locks,
# not the pipeline: if the lock is missing, the next CI apply therefore fails
# until it has been created again locally.
resource "azurerm_management_lock" "storage" {
  name       = "no-delete"
  scope      = azurerm_storage_account.jobfinder.id
  lock_level = "CanNotDelete"
  notes      = "Bewerbungsdokumente und Terraform-State. Entfernen nur bewusst und lokal."
}

# Holds the application documents. "private" means: no anonymous read access,
# only through an authenticated Entra ID identity (account keys are off).
resource "azurerm_storage_container" "application_documents" {
  name                  = "application-documents"
  storage_account_id    = azurerm_storage_account.jobfinder.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Transitional right of the earlier shared identity. In split the review
# takes over the document access. Both planned workers skip the ZIP backup
# and therefore need no document bytes; metadata stays in PostgreSQL.
resource "azurerm_role_assignment" "storage_blob_data_contributor" {
  count                = local.runtime_split ? 0 : 1
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Provides only the tenant ID now (keyvault.tf, review.tf); object_id is
# deliberately NOT read from here any more, see var.owner_object_id.
data "azurerm_client_config" "current" {}

# Own access for local development and manual checks without
# account keys. Deliberately stays on the whole account: the local
# terraform needs access to the tfstate container through it.
resource "azurerm_role_assignment" "storage_blob_data_contributor_dev" {
  scope                = azurerm_storage_account.jobfinder.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.owner_object_id
}

# Dedicated app registration for the local Docker-based hybrid worker run
# (StepStone/Remotely), which has no Managed Identity and no interactive
# az-CLI session available inside the container. Least privilege: only this
# one role on the documents container - not the tfstate container in the same
# account, since this principal's secret lives on a local disk.
resource "azurerm_role_assignment" "storage_blob_data_contributor_local_docker" {
  count                = local.runtime_split ? 0 : 1
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.local_docker_sp_object_id
  principal_type       = "ServicePrincipal"
}
