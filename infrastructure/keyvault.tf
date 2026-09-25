# Zentrale Ablage für Discord-Webhook und API-Keys, statt sie als
# Klartext-Umgebungsvariablen in der Container-App-Definition zu speichern.
resource "azurerm_key_vault" "jobfinder" {
  name                = "kv-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # Rollenbasierter Zugriff statt der älteren Access-Policy-Liste; passt zum
  # RBAC-Lernziel dieser Phase.
  rbac_authorization_enabled = true

  # Lernprojekt: Der Vault soll sich bei Bedarf ohne Aufbewahrungsfrist
  # vollständig löschen lassen. Soft-Delete selbst ist bei Key Vault
  # inzwischen immer aktiv und lässt sich nicht abschalten.
  purge_protection_enabled = false

  tags = azurerm_resource_group.jobfinder.tags
}

# Worker-Identität darf Secret-Werte lesen, aber nicht anlegen/ändern/löschen.
resource "azurerm_role_assignment" "keyvault_secrets_user_worker" {
  scope                = azurerm_key_vault.jobfinder.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Eigener Zugriff, um Secret-Werte einzutragen/zu aktualisieren – ausschließlich
# per az-CLI, niemals über Terraform (sonst landet der Wert im State).
resource "azurerm_role_assignment" "keyvault_secrets_officer_dev" {
  scope                = azurerm_key_vault.jobfinder.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.owner_object_id
}
