output "alb_dns_name" {
  value = module.service.alb_dns_name
}

output "deliveries_bucket" {
  value = module.storage.bucket_name
}

output "database_endpoint" {
  value = module.database.endpoint
}

output "ecs_cluster" {
  value = module.service.cluster_name
}
