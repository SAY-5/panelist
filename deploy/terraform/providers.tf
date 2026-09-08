provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "panelist"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }

  # LocalStack: only used for `make tf-plan-local`. Real deployments leave localstack_endpoint empty.
  skip_credentials_validation = var.localstack_endpoint != ""
  skip_requesting_account_id  = var.localstack_endpoint != ""
  skip_metadata_api_check     = var.localstack_endpoint != ""
  s3_use_path_style           = var.localstack_endpoint != ""

  dynamic "endpoints" {
    for_each = var.localstack_endpoint != "" ? [1] : []
    content {
      ec2            = var.localstack_endpoint
      ecs            = var.localstack_endpoint
      elbv2          = var.localstack_endpoint
      iam            = var.localstack_endpoint
      logs           = var.localstack_endpoint
      rds            = var.localstack_endpoint
      s3             = var.localstack_endpoint
      secretsmanager = var.localstack_endpoint
      sts            = var.localstack_endpoint
    }
  }
}
