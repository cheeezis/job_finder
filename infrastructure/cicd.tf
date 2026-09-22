# Berechtigungen für den GitHub-Actions-Service-Principal (OIDC-Anmeldung,
# siehe .github/workflows/deploy.yml). Der App-Registration/SP selbst wird
# nicht von Terraform angelegt: Terraform kann sich nicht selbst die
# Berechtigungen erteilen, die es braucht, um zu laufen (Henne-Ei-Problem),
# daher wurde er einmalig per az ad app/sp create erstellt und die
# Objekt-ID als var.github_actions_sp_object_id hinterlegt.

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
