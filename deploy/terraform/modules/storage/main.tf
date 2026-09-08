resource "random_id" "suffix" {
  byte_length = 3
}

resource "aws_s3_bucket" "deliveries" {
  bucket        = "${var.name}-deliveries-${random_id.suffix.hex}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_versioning" "deliveries" {
  bucket = aws_s3_bucket.deliveries.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "deliveries" {
  bucket = aws_s3_bucket.deliveries.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "deliveries" {
  bucket                  = aws_s3_bucket.deliveries.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
