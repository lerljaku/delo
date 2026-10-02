# ---------------------------------------------------------------------------
# Optional custom domain (var.domain_name, e.g. mtgdelo.com): ACM certificate,
# DNS records in the domain's Route 53 hosted zone, and a www -> apex redirect.
# With domain_name = "" the site stays on its *.cloudfront.net address.
# ---------------------------------------------------------------------------
locals {
  custom_domain = var.domain_name != ""
  site_aliases  = local.custom_domain ? [var.domain_name, "www.${var.domain_name}"] : []
  site_host     = local.custom_domain ? var.domain_name : aws_cloudfront_distribution.site.domain_name
}

# Route 53 creates this zone when the domain is registered there.
data "aws_route53_zone" "site" {
  count = local.custom_domain ? 1 : 0
  name  = var.domain_name
}

# CloudFront only accepts certificates from us-east-1.
resource "aws_acm_certificate" "site" {
  count                     = local.custom_domain ? 1 : 0
  provider                  = aws.us_east_1
  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for o in flatten([for c in aws_acm_certificate.site : c.domain_validation_options]) : o.domain_name => o
  }
  zone_id         = data.aws_route53_zone.site[0].zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  count                   = local.custom_domain ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_route53_record" "site" {
  for_each = toset(local.site_aliases)
  zone_id  = data.aws_route53_zone.site[0].zone_id
  name     = each.value
  type     = "A"

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

# One canonical origin, so logins (Cognito redirect + browser storage) work the same everywhere.
resource "aws_cloudfront_function" "www_redirect" {
  count   = local.custom_domain ? 1 : 0
  name    = "${local.name}-www-redirect"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var host = request.headers.host && request.headers.host.value;
      if (host && host.indexOf("www.") === 0) {
        return {
          statusCode: 301,
          statusDescription: "Moved Permanently",
          headers: { location: { value: "https://" + host.substring(4) + request.uri } },
        };
      }
      return request;
    }
  EOT
}
