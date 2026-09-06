# Deploying The Bong Connection to AWS ECS Fargate

This guide provides flexible methods to deploy your application to **AWS ECS Fargate** (serverless container compute) with persistent SQLite storage:

1. **Method 1 (GitHub Actions - Zero Local Setup)**: Fully automated CI/CD pipeline. No local Docker or AWS CLI needed!
2. **Method 2 (Local Script)**: Single-command deployment from your Mac via [`deploy-fargate.sh`](file:///Users/titlibasu/Downloads/the_bong_connection_kitchen_updated/deploy-fargate.sh).
3. **Method 3 (AWS Copilot CLI)**: AWS's official tool using [`copilot/web/manifest.yml`](file:///Users/titlibasu/Downloads/the_bong_connection_kitchen_updated/copilot/web/manifest.yml).
4. **Method 4 (AWS Management Console)**: Step-by-step click-through using the AWS Web Console.

---

## ⚡ Method 1: Automated Deployment via GitHub Actions (Zero Local Setup)

This is the fastest and easiest method because GitHub runs Docker in the cloud for you.

### Step 1: Add AWS Secrets in GitHub
1. Open your repository on GitHub: `https://github.com/droy1989/the-bong-connection-kitchen`
2. Click **Settings** -> **Secrets and variables** -> **Actions**.
3. Click **New repository secret** and add:
   - `AWS_ACCESS_KEY_ID`: Your AWS access key ID.
   - `AWS_SECRET_ACCESS_KEY`: Your AWS secret access key.
   - *(Optional)* `AWS_REGION`: Defaults to `ap-south-1` if omitted.
   - *(Optional)* `ADMIN_KEY`: Password to access `/admin` kitchen screen (defaults to a secure fallback).
   - *(Optional)* `UPI_ID`: Your stall UPI ID (e.g. `titlibasu37@okaxis`).

### Step 2: Push Your Code
Commit and push the newly added workflow to GitHub:
```bash
git add .
git commit -m "Configure AWS Fargate deployment with GitHub Actions"
git push origin main
```

### Step 3: Watch Deployment
1. Go to the **Actions** tab on GitHub.
2. You will see the **Deploy to AWS ECS Fargate** workflow running.
3. Once complete, GitHub will display your live public URLs directly in the workflow summary!

---

## 🚀 Method 2: Local Script (deploy-fargate.sh)

We've provided [`deploy-fargate.sh`](file:///Users/titlibasu/Downloads/the_bong_connection_kitchen_updated/deploy-fargate.sh) which automates ECR repository creation, Docker image building (`--platform linux/amd64`), image push, and CloudFormation stack deployment.

Run from the project root:
```bash
./deploy-fargate.sh
```

### What This Provisions Automatically:
- **Networking**: VPC, Public Subnets across 2 Availability Zones, Internet Gateway.
- **Application Load Balancer (ALB)**: Internet-facing load balancer with `/health` health check.
- **Persistent Storage (EFS)**: Amazon Elastic File System volume mounted at `/data` so your SQLite orders and products persist across restarts.
- **ECS Fargate**: Task definition with 0.25 vCPU, 512MB RAM, and desired count `1` (ensuring SQLite write safety).
- **CloudWatch Logs**: Centralized logging at `/ecs/prod-bong-connection`.

Once complete, the script outputs your public live URLs:
```text
Customer Menu:   http://prod-bong-conn-alb-xxxxxxxx.ap-south-1.elb.amazonaws.com
Kitchen Admin:   http://prod-bong-conn-alb-xxxxxxxx.ap-south-1.elb.amazonaws.com/admin
Queue Board:     http://prod-bong-conn-alb-xxxxxxxx.ap-south-1.elb.amazonaws.com/display
```

---

## 🛠️ Option 2: AWS Copilot CLI

AWS Copilot is AWS's dedicated CLI for ECS Fargate. The repository already has [`copilot/web/manifest.yml`](file:///Users/titlibasu/Downloads/the_bong_connection_kitchen_updated/copilot/web/manifest.yml) configured.

1. **Install Copilot**:
   ```bash
   brew install aws/tap/copilot-cli
   ```

2. **Initialize Environment**:
   ```bash
   copilot env init --name prod --profile default --default-config
   copilot env deploy --name prod
   ```

3. **Store Admin Secret**:
   ```bash
   copilot secret init --name ADMIN_KEY
   ```

4. **Deploy Service**:
   ```bash
   copilot deploy --name web --env prod
   ```

---

## 🖥️ Option 3: AWS Management Console (Manual)

If you prefer using the AWS Web Console:

### Step 1: Create Amazon ECR Repository & Push Image
1. Open AWS Console -> **Amazon ECR** -> **Repositories** -> **Create repository**.
2. Name it `bong-connection`. Click **Create repository**.
3. In your terminal, authenticate and push:
   ```bash
   aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com
   docker build --platform linux/amd64 -t <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/bong-connection:latest .
    docker push <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/bong-connection:latest
    ```

### Customer ordering sessions

Customers enter a name and mobile number before ordering; no OTP or SMS provider is required. The app creates a separate browser session for each customer and saves that session ID with each order. For production, serve the ALB over HTTPS and deploy with `AUTH_COOKIE_SECURE=true` so the customer-session cookie is sent only over HTTPS. The current HTTP ALB endpoint needs the default `AUTH_COOKIE_SECURE=false` temporarily or browsers will not retain the session.

### Step 2: Create EFS File System (Persistent Storage)
1. Open AWS Console -> **Amazon EFS** -> **Create file system**.
2. Name: `bong-connection-efs`. Choose your VPC.
3. Under **Access points**, create an access point:
   - Name: `data-access-point`
   - Path: `/data`
   - User ID: `1000`, Group ID: `1000`
   - Permissions: `0755`

### Step 3: Create ECS Cluster
1. Open AWS Console -> **Amazon ECS** -> **Clusters** -> **Create cluster**.
2. Cluster name: `bong-connection-cluster`.
3. Infrastructure: Check **AWS Fargate (serverless)**.
4. Click **Create**.

### Step 4: Create Task Definition
1. Open **Amazon ECS** -> **Task definitions** -> **Create new task definition with JSON** (or UI):
   - Family: `bong-connection-task`
   - Launch type: **Fargate**
   - OS/Architecture: Linux / X86_64
   - CPU: `0.25 vCPU`, Memory: `0.5 GB`
   - Container name: `app`
   - Image URI: `<ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/bong-connection:latest`
   - Port mappings: `8000` (TCP)
   - Volume: Add EFS Volume named `efs-storage`, select file system and access point created in Step 2. Mount point: `/data`.
   - Environment variables:
     - `PORT`: `8000`
     - `DB_PATH`: `/data/foodstall.db`
     - `ADMIN_KEY`: `<YOUR_ADMIN_PASSWORD>`
     - `UPI_ID`: `titlibasu37@okaxis`

### Step 5: Create ECS Service
1. In your cluster, go to **Services** -> **Create**.
2. Launch type: **Fargate**.
3. Task definition: `bong-connection-task`.
4. Service name: `bong-connection-service`.
5. Desired tasks: `1`.
6. Networking: Select VPC and public subnets.
7. Load balancing: Select **Application Load Balancer**.
   - Target group: Create new target group on port `8000`, health check path `/health`.
8. Click **Create**.

---

## 🔍 Verification & Maintenance

- **Health Check**: Verify your ALB endpoint:
  ```bash
  curl http://<ALB-DNS-NAME>/health
  # Expected: {"status":"healthy","service":"bong-connection"}
  ```
- **Kitchen Dashboard**: Log in to `http://<ALB-DNS-NAME>/admin` using your `ADMIN_KEY`.
- **View Logs**: In the AWS Console, open **CloudWatch** -> **Log groups** -> `/ecs/prod-bong-connection` to see live FastAPI and Uvicorn server logs.
- **Redeploying code changes**:
  Run `./deploy-fargate.sh` whenever you modify code. It will build and push the new image and update the running Fargate task with zero downtime.
