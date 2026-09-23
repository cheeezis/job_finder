# Berechtigungen für die GitHub-Actions-Identitäten (OIDC-Anmeldung, siehe
# .github/workflows/checks.yml). Die App-Registrierungen/SPs selbst legt
# Terraform nicht an: Es kann sich nicht selbst die Berechtigungen erteilen,
# die es braucht, um zu laufen (Henne-Ei-Problem). Sie wurden einmalig per
# az ad app/sp create erstellt, ihre Objekt-IDs stehen in variables.tf.

# Erlaubt terraform plan/apply aus der Pipeline: Ressourcen in dieser
# Resource Group anlegen, ändern und löschen.
resource "azurerm_role_assignment" "contributor_github_actions" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Contributor"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Contributor allein darf keine Rollen zuweisen; unsere Konfiguration enthält
# aber selbst mehrere azurerm_role_assignment-Ressourcen (ACR-Pull,
# Key-Vault-Zugriff, ...), die terraform apply sonst nicht verwalten könnte.
resource "azurerm_role_assignment" "rbac_admin_github_actions" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Datenebenen-Zugriff auf den State-Blob selbst; getrennt von Contributor
# (das deckt nur die Verwaltungsebene ab) und bewusst nur auf den
# tfstate-Container beschränkt, nicht den ganzen Storage-Account.
resource "azurerm_role_assignment" "state_access_github_actions" {
  scope                = "${azurerm_storage_account.jobfinder.id}/blobServices/default/containers/tfstate"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.github_actions_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Build-Identität: darf nur Images in die Registry hochladen, sonst nichts.
resource "azurerm_role_assignment" "acr_push_github_build" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPush"
  principal_id         = var.github_build_sp_object_id
  principal_type       = "ServicePrincipal"
}

# az acr login schlägt die Registry vorher über die Verwaltungsebene nach, die
# AcrPush nicht abdeckt. Reader erlaubt nur dieses Nachschlagen - weder Zugriff
# auf Images noch Änderungen an der Registry.
resource "azurerm_role_assignment" "acr_reader_github_build" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "Reader"
  principal_id         = var.github_build_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Plan-Identität für Pull Requests: sieht Ressourcen, ändert nichts. Bewusst
# ohne listSecrets - das gäbe die echten Geheimniswerte der Container Apps
# heraus. Der PR-Plan lädt deshalb nichts aus Azure nach (-refresh=false).
resource "azurerm_role_assignment" "reader_github_plan" {
  scope                = azurerm_resource_group.jobfinder.id
  role_definition_name = "Reader"
  principal_id         = var.github_plan_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Liest den State; der Plan läuft ohne Sperre (-lock=false), weil schon das
# Sperren ein Schreibvorgang auf dem State-Container wäre.
resource "azurerm_role_assignment" "state_reader_github_plan" {
  scope                = "${azurerm_storage_account.jobfinder.id}/blobServices/default/containers/tfstate"
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.github_plan_sp_object_id
  principal_type       = "ServicePrincipal"
}
