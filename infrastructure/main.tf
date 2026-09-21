# Gemeinsame Ressourcengruppe für die von Terraform verwaltete Jobfinder-Infrastruktur.
# Verweise auf andere Ressourcen machen deren Werte und Abhängigkeiten nutzbar.
resource "azurerm_resource_group" "jobfinder" {
  name     = "rg-jobfinder"
  location = var.location

  # Diese Kennzeichnungen werden von den übrigen Ressourcen übernommen.
  tags = {
    project    = "jobfinder"
    managed_by = "terraform"
  }
}

# Speichert die Docker-Images, die lokal gebaut und per Docker-Push hochgeladen werden.
resource "azurerm_container_registry" "jobfinder" {
  name                = "acrjobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  sku                 = "Basic"
  # Der Finder erhält Zugriff über eine Managed Identity statt über das Admin-Konto.
  admin_enabled = false

  tags = azurerm_resource_group.jobfinder.tags
}

# Speichert Betriebslogs der Container; die Jobfinder-Datenablage folgt separat.
resource "azurerm_log_analytics_workspace" "jobfinder" {
  name                = "law-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  # Abrechnungstarif für aufgenommene Logs; keine reservierte Speichergröße.
  sku = "PerGB2018"
  # Aufbewahrungsdauer der Logs in Tagen.
  retention_in_days = 30

  tags = azurerm_resource_group.jobfinder.tags
}

# Ausführungsumgebung für die Container-Jobs; der Finder wird separat definiert.
resource "azurerm_container_app_environment" "jobfinder" {
  name                = "cae-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.jobfinder.id

  # Consumption-Profil innerhalb einer Workload-Profiles-Umgebung.
  # Damit vermeiden wir die zuvor für Jobs ungeeignete Express-Umgebung.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Identität, mit der der Finder sein Image ohne Registry-Passwort abrufen kann.
resource "azurerm_user_assigned_identity" "jobfinder" {
  name                = "id-jobfinder-pull"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  tags = azurerm_resource_group.jobfinder.tags
}

# Erlaubt der Identität das Herunterladen von Images aus genau dieser Registry.
# Das Hochladen von Images ist in dieser Rolle nicht enthalten.
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Definiert einen manuell startbaren Finder-Lauf. Das Anlegen startet ihn noch nicht.
resource "azurerm_container_app_job" "finder" {
  name                         = "jobfinder-worker"
  resource_group_name          = azurerm_resource_group.jobfinder.name
  location                     = azurerm_resource_group.jobfinder.location
  container_app_environment_id = azurerm_container_app_environment.jobfinder.id
  workload_profile_name        = "Consumption"

  # Laufzeit auf eine Stunde begrenzen und fehlgeschlagene Läufe nicht automatisch wiederholen.
  replica_timeout_in_seconds = 3600
  replica_retry_limit        = 0

  # Zunächst manuell starten, um das Deployment kontrolliert zu prüfen.
  manual_trigger_config {
    # Pro Ausführung eine Replik; ihr erfolgreicher Abschluss beendet die Ausführung.
    parallelism              = 1
    replica_completion_count = 1
  }

  # Weist die bereits angelegte Identität diesem Job zu.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.jobfinder.id]
  }

  # Nutzt diese Identität zur Anmeldung beim Abrufen des privaten Images.
  registry {
    server   = azurerm_container_registry.jobfinder.login_server
    identity = azurerm_user_assigned_identity.jobfinder.id
  }

  # Verweist nur auf die Key-Vault-Adresse, nie auf den Secret-Wert selbst;
  # Azure löst das bei jedem Start über die zugewiesene Identität auf.
  secret {
    name                = "discord-webhook-url"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/DiscordWebhookUrl"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  secret {
    name                = "startup-jobs-api-key"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/StartupJobsApiKey"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  secret {
    name                = "jobfinder-database-url"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderDatabaseUrl"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }

  # Ohne eigenen command-Block gilt der Startbefehl aus dem Image: python run_finder.py.
  template {
    container {
      name = "jobfinder-worker"
      # Dieses Tag wurde zuvor hochgeladen; Terraform baut oder pusht das Image nicht.
      image  = "${azurerm_container_registry.jobfinder.login_server}/jobfinder:azure-v5"
      cpu    = 0.5
      memory = "1Gi"

      # Python-Ausgaben direkt ausgeben, damit Fortschritt zeitnah in den Azure-Logs erscheint.
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      # Kontoname/Container sind keine Geheimnisse; der Zugriff läuft über die
      # oben zugewiesene Managed Identity, kein gespeichertes Passwort nötig.
      env {
        name  = "JOBFINDER_DOCUMENTS_BACKEND"
        value = "blob"
      }
      env {
        name  = "JOBFINDER_STORAGE_ACCOUNT"
        value = azurerm_storage_account.jobfinder.name
      }
      env {
        name  = "JOBFINDER_STORAGE_CONTAINER"
        value = azurerm_storage_container.application_documents.name
      }
      env {
        name  = "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"
        value = azurerm_user_assigned_identity.jobfinder.client_id
      }
      env {
        name  = "JOBFINDER_REVIEW_HOST"
        value = var.review_fqdn
      }

      env {
        name        = "JOBFINDER_DATABASE_URL"
        secret_name = "jobfinder-database-url"
      }

      env {
        name        = "DISCORD_WEBHOOK_URL"
        secret_name = "discord-webhook-url"
      }
      env {
        name        = "STARTUP_JOBS_API_KEY"
        secret_name = "startup-jobs-api-key"
      }
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # Die Pull- und Key-Vault-Berechtigungen müssen vor dem Job angelegt werden.
  # Die Referenz auf die Identität allein stellt diese Reihenfolge nicht sicher.
  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.keyvault_secrets_user_worker,
  ]
}
