# Recipient of alerts; currently a single email address, as nobody else reads along.
resource "azurerm_monitor_action_group" "jobfinder_alerts" {
  name                = "ag-jobfinder-alerts"
  resource_group_name = azurerm_resource_group.jobfinder.name
  # At most 12 characters; appears as the sender short name in the alert mail.
  short_name = "jobfinder"

  email_receiver {
    name          = "owner"
    email_address = var.alert_email
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Fires as soon as a scheduled execution of the finder job fails.
#
# Uses the native "Executions" metric (dimension state=Failed) instead of a
# Log Analytics query over the structured run_failed events from
# console.py: the metric also catches crashes before the Python process
# could write any log line at all (for example image pull errors, OOM
# at start) - a log text alert would miss this case.
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

# Fast smoke detector for the AI agent: the model's token metric is available
# after minutes, cost data only after up to three days. It checks hourly
# over the last 24 hours. A normal day is at 0.6 to 1.6 million tokens,
# the daily limit in the code at about 3 million; 2 million (about
# 0.65 €) report unusual days before the daily limit applies. When the
# value drops again, an all-clear follows instead of hourly mails.
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
    # "Processed Inference Tokens": input plus output.
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

# Slow smoke detector for the whole project, so for production too.
# Cost data arrives with up to 72 hours delay; the forecast often warns
# earlier because it sees the month's trend. 25 € (billing currency of the
# subscription) is the planned maximum while PostgreSQL runs on the free
# grant of the subscription: registry about 4.35 € plus at most 20 € for the
# agent. After that about 15.55 € for the server are added, and the amount has
# to rise (docs/operations.md, section Costs). A budget only warns, it
# stops nothing. The pipeline could not manage a budget for the whole subscription
# for lack of rights at subscription level; all project resources are in this group.
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

# Traces of the AI agent (job_finder/telemetry.py): per run a tree of run,
# jobs, model and tool calls, only with IDs, numbers and verdict level.
# The data lands in the existing Log Analytics workspace. The daily cap is
# far above the expected need of a few kilobytes per run and only cuts off
# outliers; after that the traces are missing until the next day.
resource "azurerm_application_insights" "jobfinder" {
  name                 = "appi-jobfinder"
  resource_group_name  = azurerm_resource_group.jobfinder.name
  location             = azurerm_resource_group.jobfinder.location
  workspace_id         = azurerm_log_analytics_workspace.jobfinder.id
  application_type     = "other"
  retention_in_days    = 30
  daily_data_cap_in_gb = 0.1
  # Entra ID sign-in only: the connection string alone may send nothing,
  # which is why it is a normal value in the job instead of in Key Vault.
  local_authentication_enabled = false

  tags = azurerm_resource_group.jobfinder.tags
}

# Allows the worker to send traces with its managed identity.
resource "azurerm_role_assignment" "monitoring_publisher_worker" {
  scope                = azurerm_application_insights.jobfinder.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# The same for the review with its own identity (F09); until then it uses the worker's.
resource "azurerm_role_assignment" "monitoring_publisher_review" {
  count                = local.runtime_prepared ? 1 : 0
  scope                = azurerm_application_insights.jobfinder.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.review[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Operations dashboard (Azure Monitor workbook): runs, key figures, sources,
# agent cost and review loading times from the Log Analytics workspace. Free;
# the queries are in workbooks/operations.json, docs/operations.md explains them.
resource "azurerm_application_insights_workbook" "operations" {
  # Workbooks need a GUID as name; uuidv5 keeps it the same across all plans.
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
