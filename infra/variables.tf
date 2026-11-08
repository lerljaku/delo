variable "project" {
  description = "Project name, used as a prefix for all resources. Changing it on a deployed stack would replace every table, bucket and the user pool (prevent_destroy stops that apply)."
  type        = string
  default     = "mtgdelo"
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

variable "show_membership" {
  description = "Show the Membership page, nav link and upsells. Memberships set by admins work either way."
  type        = bool
  default     = false
}

variable "stripe_supporter_link" {
  description = "Stripe Payment Link for Supporter (one-time payment), e.g. https://buy.stripe.com/... Empty: no Buy button."
  type        = string
  default     = ""
}

variable "stripe_diamond_link" {
  description = "Stripe Payment Link for Diamond (monthly subscription)."
  type        = string
  default     = ""
}

variable "stripe_portal_link" {
  description = "Stripe customer portal login link, where members manage or cancel their subscription."
  type        = string
  default     = ""
}

variable "stripe_webhook_secret" {
  description = "Signing secret (whsec_...) of the Stripe webhook endpoint <site_url>/api/stripe/webhook."
  type        = string
  default     = ""
  sensitive   = true
}

variable "github_repo" {
  description = "GitHub repository URL used for 'Report an issue' links."
  type        = string
  default     = "https://github.com/lerljaku/delo"
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

variable "eventlink_throttle_rate" {
  description = "Requests per second to the anonymous extension upload, POST /api/eventlink (all users together)."
  type        = number
  default     = 0.2
}

variable "eventlink_throttle_burst" {
  description = "Burst limit of POST /api/eventlink."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  type    = number
  default = 14
}
