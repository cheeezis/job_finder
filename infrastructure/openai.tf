# Sprachmodell für den geplanten KI-Agenten. Bezahlt wird nur pro Token; ohne
# Aufrufe kostet die Ressource nichts. Die Kostenbremsen im Code
# (job_finder/agent/cost_guard.py) greifen vor jedem Aufruf; die Drossel an der
# Bereitstellung und der Token-Alarm (monitoring.tf) wirken auch dann, wenn der
# Code einen Fehler hat.
resource "azurerm_cognitive_account" "openai" {
  name                = "oai-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  kind                = "OpenAI"
  sku_name            = "S0"

  # Keine API-Schlüssel, wie beim Storage-Account: Aufrufe nur über Entra ID
  # mit den Rollen unten. Entra ID braucht dafür eine eigene Subdomain.
  local_auth_enabled    = false
  custom_subdomain_name = "oai-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"

  tags = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_cognitive_deployment" "gpt_5_mini" {
  name                 = "gpt-5-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  # Feste Version: Ein automatisches Upgrade könnte Verhalten und Preis ändern,
  # ohne dass die Preistabelle (job_finder/agent/pricing.py) davon weiß.
  # Microsoft stellt diese Version am 09.02.2027 ein.
  version_upgrade_option = "NoAutoUpgrade"

  model {
    format  = "OpenAI"
    name    = "gpt-5-mini"
    version = "2025-08-07"
  }

  # Global Standard: Bezahlung pro Token, gerechnet in einem beliebigen
  # Azure-Rechenzentrum. capacity ist die Drossel in tausend Tokens pro Minute:
  # 30 reicht für etwa 40 Steckbriefe am Tag und begrenzt einen Fehler auf grob
  # 0,40 bis 3 € pro Stunde. Das Kontingent des Abos erlaubte bis zu 1.000.
  sku {
    name     = "GlobalStandard"
    capacity = 30
  }
}

# Nur Aufrufe, keine Verwaltung: Der Worker darf das Modell nutzen, aber weder
# Bereitstellungen noch die Drossel ändern.
resource "azurerm_role_assignment" "openai_user_worker" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Eigener Zugriff für Tests vom lokalen Rechner (az login), ebenfalls ohne Schlüssel.
resource "azurerm_role_assignment" "openai_user_dev" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.owner_object_id
}
