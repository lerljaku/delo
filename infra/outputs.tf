output "site_url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "api_endpoint" {
  description = "Direct API Gateway URL (the site uses <site_url>/api instead)."
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_login_domain" {
  value = "https://${aws_cognito_user_pool_domain.users.domain}.auth.${var.region}.amazoncognito.com"
}

output "data_bucket" {
  value = aws_s3_bucket.data.id
}

output "lambda_function" {
  value = aws_lambda_function.api.function_name
}
