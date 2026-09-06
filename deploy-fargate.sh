#!/usr/bin/env bash
# ==============================================================================
# The Bong Connection — AWS ECS Fargate Automated Deployment Script
# ==============================================================================
set -euo pipefail

# Configuration defaults
AWS_REGION="${AWS_REGION:-ap-south-1}"
APP_NAME="bong-connection"
ENV_NAME="${ENV_NAME:-prod}"
ECR_REPO_NAME="bong-connection"
STACK_NAME="${ENV_NAME}-bong-connection-stack"
IMAGE_TAG="$(date +%Y%m%d%H%M%S)"

# Read optional settings from .env if present
if [ -f .env ]; then
  # export variables from .env without overriding already set env vars
  set -a
  source .env
  set +a
fi

ADMIN_KEY="${ADMIN_KEY:-change-me-in-production}"
UPI_ID="${UPI_ID:-titlibasu37@okaxis}"
UPI_NAME="${UPI_NAME:-The Bong Connection}"

echo "========================================================="
echo "   Deploying The Bong Connection to AWS ECS Fargate      "
echo "========================================================="
echo "AWS Region:   $AWS_REGION"
echo "Stack Name:   $STACK_NAME"
echo "Image Tag:    $IMAGE_TAG"
echo "========================================================="

# 1. Verify Prerequisites
if ! command -v aws &> /dev/null; then
  echo "❌ Error: AWS CLI ('aws') is not installed."
  echo "   Install it on Mac using: brew install awscli"
  echo "   Then configure credentials using: aws configure"
  exit 1
fi

if ! command -v docker &> /dev/null; then
  echo "❌ Error: Docker CLI ('docker') is not installed."
  exit 1
fi

if ! docker info &> /dev/null; then
  echo "❌ Error: Docker daemon is not running."
  echo "   Please launch Docker Desktop or start your Docker service and retry."
  exit 1
fi

echo "🔍 Verifying AWS Identity..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
echo "✅ Authenticated to AWS Account: $AWS_ACCOUNT_ID"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

# 2. Ensure ECR Repository exists
echo "📦 Ensuring ECR repository '${ECR_REPO_NAME}' exists..."
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" &> /dev/null || \
aws ecr create-repository \
  --repository-name "$ECR_REPO_NAME" \
  --region "$AWS_REGION" \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 > /dev/null
echo "✅ ECR repository ready: $ECR_URI"

# 3. Authenticate Docker with ECR
echo "🔑 Logging Docker into Amazon ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# 4. Build Docker image (targeting linux/amd64 for Fargate compatibility)
echo "🔨 Building Docker image (linux/amd64)..."
docker build --platform linux/amd64 -t "${ECR_URI}:${IMAGE_TAG}" -t "${ECR_URI}:latest" .

# 5. Push Docker image to ECR
echo "🚀 Pushing image to ECR..."
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:latest"
echo "✅ Docker image pushed to ${ECR_URI}:${IMAGE_TAG}"

# 6. Deploy or Update CloudFormation Stack
echo "☁️  Deploying AWS Fargate infrastructure via CloudFormation..."
aws cloudformation deploy \
  --template-file aws/fargate-template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      EnvironmentName="$ENV_NAME" \
      ContainerImage="${ECR_URI}:${IMAGE_TAG}" \
      AdminKey="$ADMIN_KEY" \
      UpiId="$UPI_ID" \
      UpiName="$UPI_NAME" \
  --no-fail-on-empty-changeset

echo "========================================================="
echo "🎉 Deployment Complete!"
echo "========================================================="

# Fetch outputs from CloudFormation
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" --query "Stacks[0].Outputs[?OutputKey=='WebsiteURL'].OutputValue" --output text)

echo "Customer Menu:   $WEBSITE_URL"
echo "Kitchen Admin:   $WEBSITE_URL/admin"
echo "Queue Board:     $WEBSITE_URL/display"
echo "Health Check:    $WEBSITE_URL/health"
echo "========================================================="
