variable "project" {
  description = "Project name, used as a prefix for all resources."
  type        = string
  default     = "delo"
}

variable "environment" {
  description = "Deployment environment (e.g. prod, dev). Lets several stacks share one AWS account."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region for everything except CloudFront (global)."
  type        = string
  default     = "eu-west-1"
}

variable "domain_name" {
  description = "Custom domain for the site, e.g. mtgdelo.com (www redirects to it). Needs a Route 53 hosted zone for the domain, which Route 53 creates when you register it there. Empty: use the *.cloudfront.net address."
  type        = string
  default     = ""
}

variable "operator_name" {
  description = "Who runs the site (person or organisation), named as data controller on the privacy page."
  type        = string
  default     = ""
}

variable "privacy_contact" {
  description = "Email for privacy requests (access, removal of your name from results), shown on the privacy page."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository URL used for 'Report an issue' links."
  type        = string
  default     = "https://github.com/lerljaku/esl"
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
