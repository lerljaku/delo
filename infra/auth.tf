# ---------------------------------------------------------------------------
# Cognito: email sign-up with verification, Hosted UI, "admin" group.
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "users" {
  name                     = "${local.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 10
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "Your EloHell verification code"
    email_message        = "Your EloHell verification code is {####}"
  }

  # Cognito's built-in email sender allows ~50 emails/day. Switch to SES
  # (email_configuration { email_sending_account = "DEVELOPER" ... }) when you outgrow it.
}

resource "aws_cognito_user_pool_domain" "users" {
  domain       = "${local.name}-${random_id.suffix.hex}"
  user_pool_id = aws_cognito_user_pool.users.id
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.users.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["https://${aws_cloudfront_distribution.site.domain_name}/callback.html"]
  logout_urls                          = ["https://${aws_cloudfront_distribution.site.domain_name}/index.html"]
  id_token_validity                    = 12
  access_token_validity                = 12
  token_validity_units {
    id_token     = "hours"
    access_token = "hours"
  }
  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.users.id
  description  = "Can upload tournaments, approve claims and publish release notes"
}

resource "aws_cognito_user" "admin" {
  for_each     = toset(var.admin_emails)
  user_pool_id = aws_cognito_user_pool.users.id
  username     = each.value
  attributes = {
    email          = each.value
    email_verified = true
  }
  desired_delivery_mediums = ["EMAIL"]
}

resource "aws_cognito_user_in_group" "admin" {
  for_each     = toset(var.admin_emails)
  user_pool_id = aws_cognito_user_pool.users.id
  group_name   = aws_cognito_user_group.admin.name
  username     = aws_cognito_user.admin[each.key].sub
}
