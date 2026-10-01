terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state (recommended once more than one person deploys). Create the bucket
  # and lock table once by hand, then uncomment and run `terraform init -migrate-state`.
  # backend "s3" {
  #   bucket         = "esl-terraform-state-<account-id>"
  #   key            = "prod/terraform.tfstate"
  #   region         = "eu-central-1"
  #   dynamodb_table = "esl-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region  = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
