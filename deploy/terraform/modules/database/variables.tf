variable "name" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "app_security_group" { type = string }
variable "instance_class" { type = string }
variable "allocated_storage" { type = number }
