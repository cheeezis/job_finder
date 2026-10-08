# Central store for the Discord webhook and API keys, instead of keeping them
# as plain-text environment variables in the container app definition.
resource "azurerm_key_vault" "jobfinder" {
  name                = "kv-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # Role-based access instead of the older access policy list; fits the
  # RBAC learning goal of this phase.
  rbac_authorization_enabled = true

  # Learning project: the vault should be deletable completely without a
  # retention period when needed. Soft delete itself is now always active
  # for Key Vault and cannot be switched off.
  purge_protection_enabled = false

  tags = azurerm_resource_group.jobfinder.tags

  # A deliberate deletion stays possible, but only after a code change:
  # Terraform aborts a plan that would delete or recreate the vault.
  lifecycle {
    prevent_destroy = true
  }
}

# The worker identity may read secret values, but not create/change/delete them.
resource "azurerm_role_assignment" "keyvault_secrets_user_worker" {
  count                = local.runtime_split ? 0 : 1
  scope                = azurerm_key_vault.jobfinder.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Own access to enter/update secret values - only through the az CLI,
# never through Terraform (otherwise the value ends up in the state).
resource "azurerm_role_assignment" "keyvault_secrets_officer_dev" {
  scope                = azurerm_key_vault.jobfinder.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.owner_object_id
}
