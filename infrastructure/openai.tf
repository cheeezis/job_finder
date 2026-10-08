# Language model for the AI agent. Only tokens are paid for; without
# calls the resource costs nothing. The cost brakes in the code
# (job_finder/agent/cost_guard.py) apply before every call; the throttle on the
# deployment and the token alert (monitoring.tf) work even when the code
# has a bug.
resource "azurerm_cognitive_account" "openai" {
  name                = "oai-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  kind                = "OpenAI"
  sku_name            = "S0"

  # No API keys, as for the storage account: calls only through Entra ID
  # with the roles below. Entra ID needs a subdomain of its own for that.
  local_auth_enabled    = false
  custom_subdomain_name = "oai-jobfinder-${substr(sha256(var.subscription_id), 0, 8)}"

  tags = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_cognitive_deployment" "gpt_5_mini" {
  name                 = "gpt-5-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  # Fixed version: an automatic upgrade could change behaviour and price
  # without the price table (job_finder/agent/pricing.py) knowing about it.
  # Microsoft retires this version on 09.02.2027.
  version_upgrade_option = "NoAutoUpgrade"

  model {
    format  = "OpenAI"
    name    = "gpt-5-mini"
    version = "2025-08-07"
  }

  # Global Standard: paid per token, computed in any Azure data centre.
  # capacity is the throttle in thousands of tokens per minute.
  # Azure checks it in advance with an estimate from character count and maximum
  # answer length, which is two to three times above the real consumption: a
  # request with a long listing is estimated at over 30,000; at 30 it never
  # got through. 60 allows about two requests per minute and limits an error
  # in the real consumption to roughly 0.40 to 3 € per hour. The subscription's
  # quota allowed up to 1,000.
  sku {
    name     = "GlobalStandard"
    capacity = 60
  }
}

# Calls only, no management: the worker may use the model, but change neither
# deployments nor the throttle.
resource "azurerm_role_assignment" "openai_user_worker" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# The local hybrid run writes the fact sheets of its jobs itself instead of
# waiting for the next Azure run; calls only as well.
resource "azurerm_role_assignment" "openai_user_local_docker" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.local_docker_sp_object_id
  principal_type       = "ServicePrincipal"
}

# Own access for tests from the local computer (az login), also without keys.
resource "azurerm_role_assignment" "openai_user_dev" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.owner_object_id
}
