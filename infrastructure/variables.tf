variable "subscription_id" {
  description = "Azure-Subscription, in der die Jobfinder-Infrastruktur erstellt wird."
  type        = string
}

variable "location" {
  description = "Azure-Region für die Jobfinder-Ressourcen."
  type        = string
  default     = "francecentral"
}