variable "subscription_id" {
  description = "Azure subscription in which the job finder infrastructure is created."
  type        = string
}

variable "location" {
  description = "Azure region for the job finder resources."
  type        = string
  default     = "francecentral"
}

variable "postgres_admin_password" {
  description = "Local admin secret; stored neither in the plan nor in the state."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "review_aad_client_id" {
  description = "App ID of the Entra ID registration for Easy Auth created with az ad app create."
  type        = string
  default     = "323cccd5-c4d1-4380-a5c7-818cc20ffb0b"
}

variable "review_fqdn" {
  description = "Predictable host name of the review container app; it cannot be derived from the resource itself, since it would then refer to itself."
  type        = string
  sensitive   = true
}

variable "local_docker_sp_object_id" {
  description = "Object ID of the service principal for the local Docker worker run (StepStone/Remotely), created with az ad sp create."
  type        = string
  default     = "f270644a-c084-422f-a1db-ae500701dca0"
}

variable "alert_email" {
  description = "Recipient address for Azure Monitor alerts (failed finder runs, cost warnings)."
  type        = string
  sensitive   = true
}

variable "image_tag" {
  description = "Tag of the jobfinder image only for the initial setup of worker and review app. Afterwards the CI/CD pipeline sets the image; Terraform ignores changes to it (lifecycle.ignore_changes)."
  type        = string
  default     = "azure-v9"
}

variable "owner_object_id" {
  description = "Object ID of the personal Entra ID account. Fixed on purpose instead of resolved through data.azurerm_client_config.current: otherwise a Terraform run by the CI/CD pipeline would move the developer permissions and the review app sign-in from the own person to the GitHub Actions principal."
  type        = string
  default     = "e417d473-3d02-48ee-9a4d-b2fab9bf84f1"
}

variable "github_actions_sp_object_id" {
  description = "Object ID of the service principal for the GitHub Actions CI/CD pipeline (OIDC, no stored secret), created with az ad sp create."
  type        = string
  default     = "746f1edf-e808-4861-a7bb-af7c491ac17a"
}

variable "github_build_sp_object_id" {
  description = "Object ID of the service principal jobfinder-github-build: may only push images to the registry (build job on main)."
  type        = string
  default     = "904edb79-936b-42a3-895f-2a271c9e8879"
}

variable "github_plan_sp_object_id" {
  description = "Object ID of the service principal jobfinder-github-plan: read-only (terraform plan in pull requests)."
  type        = string
  default     = "6017ba66-8ecd-4508-b182-f1d84b68b4d4"
}

variable "postgres_client_ipv4" {
  description = "Current public IPv4 of the computer for the targeted database access."
  type        = string

  validation {
    condition = (
      can(cidrnetmask("${var.postgres_client_ipv4}/32")) &&
      var.postgres_client_ipv4 != "0.0.0.0" &&
      var.postgres_client_ipv4 != "255.255.255.255"
    )
    error_message = "Give a single IPv4 address; 0.0.0.0 is not allowed."
  }
}
