variable "region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type        = string
  description = "dev, staging or prod"
}

variable "image" {
  type        = string
  description = "Container image for the API, e.g. an ECR URI with tag"
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "attention_fraction" {
  type    = number
  default = 0.1
}

variable "localstack_endpoint" {
  type        = string
  default     = ""
  description = "Set to http://localhost:4569 to plan against LocalStack"
}
