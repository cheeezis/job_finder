# Empfänger für Alerts; aktuell nur eine E-Mail-Adresse, da niemand sonst mitliest.
resource "azurerm_monitor_action_group" "jobfinder_alerts" {
  name                = "ag-jobfinder-alerts"
  resource_group_name = azurerm_resource_group.jobfinder.name
  # Max. 12 Zeichen; erscheint als Absenderkürzel in der Alert-Mail.
  short_name = "jobfinder"

  email_receiver {
    name          = "owner"
    email_address = var.alert_email
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Schlägt an, sobald eine geplante Ausführung des Finder-Jobs fehlschlägt.
#
# Nutzt die native "Executions"-Metrik (Dimension state=Failed) statt einer
# Log-Analytics-Abfrage über die strukturierten run_failed-Events aus
# console.py: die Metrik erfasst auch Abstürze, bevor der Python-Prozess
# überhaupt eine Log-Zeile schreiben konnte (z. B. Image-Pull-Fehler, OOM
# beim Start) - ein Log-Text-Alert würde diesen Fall verpassen.
resource "azurerm_monitor_metric_alert" "finder_run_failed" {
  name                = "alert-jobfinder-run-failed"
  resource_group_name = azurerm_resource_group.jobfinder.name
  scopes              = [azurerm_container_app_job.finder.id]
  description         = "Ein geplanter Finder-Lauf ist fehlgeschlagen."
  severity            = 2
  frequency           = "PT15M"
  window_size         = "PT30M"

  criteria {
    metric_namespace = "Microsoft.App/jobs"
    metric_name      = "Executions"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "state"
      operator = "Include"
      values   = ["Failed"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.jobfinder_alerts.id
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Schneller Rauchmelder für den KI-Agenten: Die Token-Metrik des Modells liegt
# nach Minuten vor, Kostendaten erst nach bis zu drei Tagen. Geprüft wird
# stündlich über die letzten 24 Stunden. Ein normaler Tag liegt bei 0,6 bis
# 1,6 Mio. Tokens, die Tagesgrenze im Code bei etwa 3 Mio.; 2 Mio. (etwa
# 0,65 €) melden ungewöhnliche Tage, bevor die Tagesgrenze greift. Sinkt der
# Wert wieder, folgt eine Entwarnung statt stündlicher Mails.
resource "azurerm_monitor_metric_alert" "model_tokens_high" {
  name                = "alert-jobfinder-model-tokens"
  resource_group_name = azurerm_resource_group.jobfinder.name
  scopes              = [azurerm_cognitive_account.openai.id]
  description         = "Das Sprachmodell hat in 24 Stunden mehr als 2 Mio. Tokens verarbeitet."
  severity            = 2
  frequency           = "PT1H"
  window_size         = "P1D"

  criteria {
    metric_namespace = "Microsoft.CognitiveServices/accounts"
    # "Processed Inference Tokens": Eingabe plus Ausgabe.
    metric_name = "TokenTransaction"
    aggregation = "Total"
    operator    = "GreaterThan"
    threshold   = 2000000
  }

  action {
    action_group_id = azurerm_monitor_action_group.jobfinder_alerts.id
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Langsamer Rauchmelder für das ganze Projekt, also auch für die Produktion.
# Kostendaten kommen mit bis zu 72 Stunden Verzögerung; die Hochrechnung warnt
# oft früher, weil sie den Trend des Monats sieht. 25 € (Rechnungswährung des
# Abos) sind das geplante Maximum, solange PostgreSQL im kostenlosen Kontingent
# der Subscription läuft: Registry etwa 4,35 € plus höchstens 20 € für den
# Agenten. Danach kommen etwa 15,55 € für den Server hinzu, und der Betrag muss
# steigen (docs/operations.md, Abschnitt Kosten). Ein Budget warnt nur, es
# stoppt nichts. Ein Budget für das ganze Abo könnte die Pipeline mangels Rechten
# auf Abo-Ebene nicht verwalten; alle Projektressourcen liegen in dieser Gruppe.
resource "azurerm_consumption_budget_resource_group" "jobfinder" {
  name              = "budget-jobfinder"
  resource_group_id = azurerm_resource_group.jobfinder.id
  amount            = 25
  time_grain        = "Monthly"

  time_period {
    start_date = "2026-09-01T00:00:00Z"
  }

  notification {
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_groups = [azurerm_monitor_action_group.jobfinder_alerts.id]
  }

  notification {
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_groups = [azurerm_monitor_action_group.jobfinder_alerts.id]
  }

  notification {
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_groups = [azurerm_monitor_action_group.jobfinder_alerts.id]
  }

  notification {
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_groups = [azurerm_monitor_action_group.jobfinder_alerts.id]
  }
}

# Traces des KI-Agenten (job_finder/telemetry.py): je Lauf ein Baum aus Lauf,
# Stellen, Modell- und Werkzeugaufrufen, nur mit IDs, Zahlen und Fazit-Stufe.
# Die Daten landen im bestehenden Log-Analytics-Workspace. Das Tageslimit liegt
# weit über dem erwarteten Bedarf von wenigen Kilobyte je Lauf und kappt nur
# Ausreißer; danach fehlen die Traces bis zum nächsten Tag.
resource "azurerm_application_insights" "jobfinder" {
  name                 = "appi-jobfinder"
  resource_group_name  = azurerm_resource_group.jobfinder.name
  location             = azurerm_resource_group.jobfinder.location
  workspace_id         = azurerm_log_analytics_workspace.jobfinder.id
  application_type     = "other"
  retention_in_days    = 30
  daily_data_cap_in_gb = 0.1
  # Nur Entra-ID-Anmeldung: Die Verbindungszeichenfolge allein darf nichts
  # senden, deshalb steht sie als normaler Wert im Job statt im Key Vault.
  local_authentication_enabled = false

  tags = azurerm_resource_group.jobfinder.tags
}

# Erlaubt dem Worker, mit seiner Managed Identity Traces zu senden.
resource "azurerm_role_assignment" "monitoring_publisher_worker" {
  scope                = azurerm_application_insights.jobfinder.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Ebenso der Review mit ihrer eigenen Identität (F09); bis dahin nutzt sie die des Workers.
resource "azurerm_role_assignment" "monitoring_publisher_review" {
  count                = local.runtime_prepared ? 1 : 0
  scope                = azurerm_application_insights.jobfinder.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.review[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Betriebs-Dashboard (Azure-Monitor-Arbeitsmappe): Läufe, Kennzahlen, Quellen,
# Agentenkosten und Review-Ladezeiten aus dem Log-Analytics-Workspace. Kostenlos;
# die Abfragen stehen in workbooks/operations.json, docs/operations.md erklärt sie.
resource "azurerm_application_insights_workbook" "operations" {
  # Arbeitsmappen brauchen eine GUID als Namen; uuidv5 hält sie über alle Pläne gleich.
  name                = uuidv5("url", "https://github.com/cheeezis/job_finder/workbooks/operations")
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  display_name        = "Job Finder – Betrieb"
  source_id           = lower(azurerm_log_analytics_workspace.jobfinder.id)
  data_json = templatefile("${path.module}/workbooks/operations.json", {
    workspace_id = azurerm_log_analytics_workspace.jobfinder.id
  })

  tags = azurerm_resource_group.jobfinder.tags
}
