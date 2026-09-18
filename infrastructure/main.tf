resource "azurerm_resource_group" "jobfinder" {
  name     = "rg-jobfinder"
  location = var.location

  tags = {
    project    = "jobfinder"
    managed_by = "terraform"
  }
}

resource "azurerm_container_registry" "jobfinder" {
  name                = "acrjobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  sku                 = "Basic"
  admin_enabled       = false

  tags = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_log_analytics_workspace" "jobfinder" {
  name                = "law-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_container_app_environment" "jobfinder" {
  name                = "cae-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.jobfinder.id

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = azurerm_resource_group.jobfinder.tags
}
