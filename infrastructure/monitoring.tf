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
