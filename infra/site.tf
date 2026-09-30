# ---------------------------------------------------------------------------
# Static site: private S3 bucket behind CloudFront. CloudFront also routes
# /api/* to API Gateway so the browser sees a single origin (no CORS).
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-site-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.name}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } }
    }]
  })
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# The API sends Cache-Control: no-store, so nothing is cached. The Authorization header
# must be part of a cache policy to reach the origin, which requires max TTL > 0.
resource "aws_cloudfront_cache_policy" "api" {
  name        = "${local.name}-api"
  min_ttl     = 0
  default_ttl = 0
  max_ttl     = 1

  parameters_in_cache_key_and_forwarded_to_origin {
    headers_config {
      header_behavior = "whitelist"
      headers {
        items = ["Authorization"]
      }
    }
    query_strings_config {
      query_string_behavior = "all"
    }
    cookies_config {
      cookie_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = local.name

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id   = "api"
    domain_name = replace(aws_apigatewayv2_api.api.api_endpoint, "https://", "")
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "api"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = aws_cloudfront_cache_policy.api.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Custom domain: add `aliases` and an ACM certificate from us-east-1 here.
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# ---------------------------------------------------------------------------
# Frontend files. A short max-age means deploys are visible within 5 minutes
# without CloudFront invalidations.
# ---------------------------------------------------------------------------
locals {
  frontend_dir = "${path.module}/../frontend"
  mime_types = {
    html = "text/html; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    css  = "text/css; charset=utf-8"
    png  = "image/png"
    svg  = "image/svg+xml"
    ico  = "image/x-icon"
    json = "application/json"
  }
}

resource "aws_s3_object" "frontend" {
  for_each      = { for f in fileset(local.frontend_dir, "**") : f => f if f != "config.js" }
  bucket        = aws_s3_bucket.site.id
  key           = each.value
  source        = "${local.frontend_dir}/${each.value}"
  etag          = filemd5("${local.frontend_dir}/${each.value}")
  content_type  = lookup(local.mime_types, reverse(split(".", each.value))[0], "application/octet-stream")
  cache_control = "public, max-age=300"
}

locals {
  frontend_config = {
    apiBase    = "/api"
    githubRepo = var.github_repo
    auth = {
      mode        = "cognito"
      domain      = "https://${aws_cognito_user_pool_domain.users.domain}.auth.${var.region}.amazoncognito.com"
      clientId    = aws_cognito_user_pool_client.web.id
      redirectUri = "https://${aws_cloudfront_distribution.site.domain_name}/callback.html"
      logoutUri   = "https://${aws_cloudfront_distribution.site.domain_name}/index.html"
    }
  }
}

resource "aws_s3_object" "config" {
  bucket        = aws_s3_bucket.site.id
  key           = "config.js"
  content_type  = local.mime_types.js
  cache_control = "public, max-age=300"
  content       = "window.ESL_CONFIG = ${jsonencode(local.frontend_config)};\n"
}
