# tflint-Regeln für den Terraform-Code; der azurerm-Regelsatz prüft
# Azure-spezifische Werte wie SKUs, Regionen und Größen.
plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
