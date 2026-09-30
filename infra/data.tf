locals {
  name = "${var.project}-${var.environment}"
}

resource "random_id" "suffix" {
  byte_length = 3
}

# ---------------------------------------------------------------------------
# S3: tournament source data (raw uploads + normalized JSON) and release notes.
# Versioned: this is the source of truth for recalculation.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-data-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# DynamoDB (on-demand: pay per request, $0 when idle)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "tournaments" {
  name         = "${local.name}-tournaments"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "tournamentId"

  attribute {
    name = "tournamentId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "players" {
  name         = "${local.name}-players"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "playerId"
  range_key    = "SK"

  attribute {
    name = "playerId"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "ladder"
    type = "S"
  }
  attribute {
    name = "rating"
    type = "N"
  }

  # Only LADDER#<ladder> items carry `ladder` + `rating`, so this sparse index is the leaderboard.
  global_secondary_index {
    name               = "leaderboard"
    hash_key           = "ladder"
    range_key          = "rating"
    projection_type    = "INCLUDE"
    non_key_attributes = ["displayName", "hidden", "membership", "summary"]
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "accounts" {
  name         = "${local.name}-accounts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sub"

  attribute {
    name = "sub"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "release_notes" {
  name         = "${local.name}-release-notes"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "version"

  attribute {
    name = "version"
    type = "S"
  }
}

# ---------------------------------------------------------------------------
# Release notes from docs/release-notes/*.md are deployed with the stack.
# File format: "key: value" header lines, a line with "---", then Markdown.
# ---------------------------------------------------------------------------
locals {
  release_notes_dir = "${path.module}/../docs/release-notes"
  release_note_parts = {
    for f in fileset(local.release_notes_dir, "*.md") :
    trimsuffix(f, ".md") => split("\n---\n", replace(file("${local.release_notes_dir}/${f}"), "\r\n", "\n"))
  }
  release_notes = {
    for version, parts in local.release_note_parts : version => {
      header = parts[0]
      body   = trimspace(join("\n---\n", slice(parts, 1, length(parts))))
    }
  }
}

resource "aws_s3_object" "release_note" {
  for_each     = local.release_notes
  bucket       = aws_s3_bucket.data.id
  key          = "release-notes/${each.key}.md"
  content      = each.value.body
  content_type = "text/markdown"
}

resource "aws_dynamodb_table_item" "release_note" {
  for_each   = local.release_notes
  table_name = aws_dynamodb_table.release_notes.name
  hash_key   = aws_dynamodb_table.release_notes.hash_key
  item = jsonencode({
    version = { S = each.key }
    title   = { S = trimspace(one(regex("(?m)^title:(.*)$", each.value.header))) }
    date    = { S = trimspace(one(regex("(?m)^date:(.*)$", each.value.header))) }
    summary = { S = trimspace(try(one(regex("(?m)^summary:(.*)$", each.value.header)), "")) }
    s3Key   = { S = aws_s3_object.release_note[each.key].key }
  })
}
