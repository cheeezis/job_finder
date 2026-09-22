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
