# 1. Define the Cloud Provider
provider "aws" {
  region = "ap-southeast-2" # Sydney region
}

# 2. Create a Private Network (VPC)
# So the vault is not accessible directly from the open internet
resource "aws_vpc" "vault_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true

  tags = {
    Name = "secure-vault-network"
  }
}

# 3. Secure Security Group (The Firewall)
resource "aws_security_group" "vault_sg" {
  name        = "vault-security-group"
  description = "Allow restricted access to the secure vault"
  vpc_id      = aws_vpc.vault_vpc.id

  # Only allow HTTPS traffic on 443
  ingress {
    description = "Allow HTTPS from trusted public IP"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["1.2.3.4/32"] # Replace with your specific IP
  }

  # Restrict outbound access to HTTPS only.
  egress {
    description = "Allow HTTPS egress for updates and package repositories"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Create a subnet and attach the SG to an ENI so the SG is not orphaned.
resource "aws_subnet" "vault_subnet" {
  vpc_id            = aws_vpc.vault_vpc.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "ap-southeast-2a"
}

resource "aws_network_interface" "vault_eni" {
  subnet_id       = aws_subnet.vault_subnet.id
  security_groups = [aws_security_group.vault_sg.id]
}

# Lock down the default security group for this VPC.
resource "aws_default_security_group" "vault_default_sg" {
  vpc_id = aws_vpc.vault_vpc.id
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_kms_key" "vpc_flow_logs_kms" {
  description             = "KMS key for VPC flow logs CloudWatch encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAccountRootFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogsUseOfKey"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.name}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/secure-vault/flow-logs"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.vpc_flow_logs_kms.arn
}

resource "aws_iam_role" "vpc_flow_logs_role" {
  name = "secure-vault-vpc-flow-logs-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "vpc_flow_logs_policy" {
  name = "secure-vault-vpc-flow-logs-policy"
  role = aws_iam_role.vpc_flow_logs_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.vpc_flow_logs.arn}:*"
      }
    ]
  })
}

resource "aws_flow_log" "vault_vpc_flow_log" {
  vpc_id               = aws_vpc.vault_vpc.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_group_name       = aws_cloudwatch_log_group.vpc_flow_logs.name
  iam_role_arn         = aws_iam_role.vpc_flow_logs_role.arn
}
