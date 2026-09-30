variable "project" {
  description = "Project name, used as a prefix for all resources."
  type        = string
  default     = "esl"
}

variable "environment" {
  description = "Deployment environment (e.g. prod, dev). Lets several stacks share one AWS account."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region for everything except CloudFront (global)."
  type        = string
  default     = "eu-central-1"
}

variable "github_repo" {
  description = "GitHub repository URL used for 'Report an issue' links."
  type        = string
  default     = "https://github.com/your-org/esl"
}

variable "admin_emails" {
  description = "Emails that get an admin account. Cognito emails them a temporary password."
  type        = list(string)
  default     = []
}

variable "api_throttle_rate" {
  description = "Steady-state API requests per second (protects against cost spikes)."
  type        = number
  default     = 20
}

variable "api_throttle_burst" {
  description = "API burst limit."
  type        = number
  default     = 50
}

variable "log_retention_days" {
  type    = number
  default = 14
}
