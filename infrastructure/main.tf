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
