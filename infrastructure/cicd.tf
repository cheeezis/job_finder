# Permissions of the GitHub Actions identities (OIDC sign-in, see
# .github/workflows/checks.yml). Terraform does not create the app
# registrations/SPs itself: it cannot grant itself the permissions it
# needs to run (chicken-and-egg problem). They were created once with
# az ad app/sp create; their object IDs are in variables.tf.

# Allows terraform plan/apply from the pipeline: create, change and delete
# resources in this resource group.
resource "azurerm_role_assignment" "contributor_github_actions" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Contributor"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Contributor alone may not assign roles; our configuration itself contains
# several azurerm_role_assignment resources (ACR pull, Key Vault access,
# ...), which terraform apply could not manage otherwise.
resource "azurerm_role_assignment" "rbac_admin_github_actions" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Data plane access to the state blob itself; separate from Contributor
# (which covers only the management plane) and deliberately limited to the
# tfstate container, not the whole storage account.
resource "azurerm_role_assignment" "state_access_github_actions" {
  scope                = "${azurerm_storage_account.jobfinder.id}/blobServices/default/containers/tfstate"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Build identity: may only push images to the registry, nothing else.
resource "azurerm_role_assignment" "acr_push_github_build" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPush"
  principal_id         = var.github_build_sp_object_id
  principal_type       = "ServicePrincipal"
}

# az acr login first looks the registry up through the management plane,
# which AcrPush does not cover. Reader allows only this lookup - neither
# access to images nor changes to the registry.
resource "azurerm_role_assignment" "acr_reader_github_build" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "Reader"
  principal_id         = var.github_build_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Plan identity for pull requests: sees resources, changes nothing. Deliberately
# without listSecrets - that would reveal the real secret values of the
# container apps. The PR plan therefore loads nothing from Azure (-refresh=false).
resource "azurerm_role_assignment" "reader_github_plan" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Reader"
  principal_id         = var.github_plan_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Reads the state; the plan runs without a lock (-lock=false), because the lock
# itself would already be a write to the state container.
resource "azurerm_role_assignment" "state_reader_github_plan" {
  scope                = "${azurerm_storage_account.jobfinder.id}/blobServices/default/containers/tfstate"
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.github_plan_sp_object_id
  principal_type       = "ServicePrincipal"
}
