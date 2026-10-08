# tflint rules for the Terraform code; the azurerm rule set checks
# Azure-specific values such as SKUs, regions and sizes.
plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
