locals {
  name = "panelist-${var.environment}"
}

module "network" {
  source   = "./modules/network"
  name     = local.name
  vpc_cidr = var.vpc_cidr
}

module "storage" {
  source      = "./modules/storage"
  name        = local.name
  environment = var.environment
}

module "database" {
  source             = "./modules/database"
  name               = local.name
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.private_subnet_ids
  app_security_group = module.service.service_security_group_id
  instance_class     = var.db_instance_class
  allocated_storage  = var.db_allocated_storage
}

module "service" {
  source             = "./modules/service"
  name               = local.name
  region             = var.region
  vpc_id             = module.network.vpc_id
  public_subnet_ids  = module.network.public_subnet_ids
  image              = var.image
  desired_count      = var.desired_count
  cpu                = var.cpu
  memory             = var.memory
  log_retention_days = var.log_retention_days
  database_url_arn   = module.database.database_url_secret_arn
  deliveries_bucket  = module.storage.bucket_name
  deliveries_arn     = module.storage.bucket_arn
  attention_fraction = var.attention_fraction
  attention_key_arn  = aws_secretsmanager_secret.attention_key.arn
}

# The key that salts the attention-check schedule. A task environment variable would be
# readable by anyone who can describe the task definition, so it is injected as a secret.
resource "aws_secretsmanager_secret" "attention_key" {
  name                    = "${local.name}/attention-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "attention_key" {
  secret_id     = aws_secretsmanager_secret.attention_key.id
  secret_string = var.attention_key
}
