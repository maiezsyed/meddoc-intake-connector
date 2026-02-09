# Delivery Finance App - Deployment Guide

This guide walks you through deploying the Delivery Finance App to Google Cloud Platform.

## Files You Need

```
delivery-finance-app/
├── app/
│   ├── main.py              # Streamlit application
│   ├── universal_etl.py     # ETL processing logic
│   ├── requirements.txt     # Python dependencies
│   ├── Dockerfile           # Container definition
│   ├── cloudbuild.yaml      # Cloud Build deployment
│   └── .dockerignore        # Docker build optimization
└── terraform/
    └── main.tf              # Infrastructure as code
```

**That's it - just these 7 files.**

## Prerequisites

1. **GCP Project** with billing enabled
2. **gcloud CLI** installed and authenticated
3. **Terraform** >= 1.0.0 installed
4. **Docker** installed (for local testing only)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Browser                             │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Cloud Run (Streamlit)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   Upload    │  │   Process   │  │     Chat with Gemini    │ │
│  │   Excel     │  │   Sheets    │  │   (Project Q&A)         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌──────────────────┐ ┌────────────────┐ ┌─────────────────┐
│    BigQuery      │ │   Vertex AI    │ │ Artifact        │
│  (Data Store)    │ │   (Gemini)     │ │ Registry        │
└──────────────────┘ └────────────────┘ └─────────────────┘
```

## Step 1: Set Up Your GCP Project

```bash
# Set your project ID
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"

# Authenticate
gcloud auth login
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
    bigquery.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    sourcerepo.googleapis.com
```

## Step 1b: Set Up GCP Source Repository (Optional)

If you want to use GCP Source Repositories instead of GitHub:

```bash
# Create repository
gcloud source repos create delivery-finance-app

# Clone it locally
gcloud source repos clone delivery-finance-app ~/delivery-finance-app
cd ~/delivery-finance-app

# Copy files (from wherever you downloaded them)
cp -r /path/to/app ./
cp -r /path/to/terraform ./

# Commit and push
git add .
git commit -m "Initial commit"
git push origin master
```

## Step 2: Deploy Infrastructure with Terraform

Terraform manages: **BigQuery, Artifact Registry, Service Account, IAM**

```bash
cd terraform

# Initialize Terraform
terraform init

# Review the plan
terraform plan -var="project_id=$PROJECT_ID"

# Apply the configuration
terraform apply -var="project_id=$PROJECT_ID"

# Note the outputs
terraform output
```

This creates:
- BigQuery dataset with 8 tables (projects, allocations, rate_cards, costs, etc.)
- Service account with BigQuery, Vertex AI, and logging permissions
- Artifact Registry for Docker images
- IAM permissions for Cloud Build to deploy

## Step 3: Build and Deploy the Application

Cloud Build manages: **Docker builds and Cloud Run deployments**

```bash
cd app

# Deploy using Cloud Build (recommended)
gcloud builds submit --config=cloudbuild.yaml .

# Get your app URL
gcloud run services describe delivery-finance-app \
    --region us-central1 \
    --format="value(status.url)"
```

### Alternative: Manual Deployment

```bash
cd app

# Build the Docker image
docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/delivery-finance-app/app:latest .

# Authenticate Docker to Artifact Registry
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Push the image
docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/delivery-finance-app/app:latest

# Deploy to Cloud Run
gcloud run deploy delivery-finance-app \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/delivery-finance-app/app:latest \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --service-account delivery-finance-app@${PROJECT_ID}.iam.gserviceaccount.com \
    --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET_ID=delivery_finance,CLOUD_RUN_REGION=${REGION}" \
    --memory 2Gi \
    --cpu 2 \
    --min-instances 0 \
    --max-instances 5
```

## Step 4: Access the Application

After deployment, get the Cloud Run URL:

```bash
gcloud run services describe delivery-finance-app --region $REGION --format="value(status.url)"
```

Open the URL in your browser.

## Step 5: Local Development

For local testing:

```bash
cd app

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GCP_PROJECT_ID="your-project-id"
export BQ_DATASET_ID="delivery_finance"
export CLOUD_RUN_REGION="us-central1"

# Authenticate with GCP (for BigQuery and Vertex AI access)
gcloud auth application-default login

# Run Streamlit
streamlit run main.py
```

The app will be available at http://localhost:8501

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCP_PROJECT_ID` | Your GCP project ID | Required |
| `BQ_DATASET_ID` | BigQuery dataset name | `delivery_finance` |
| `GEMINI_MODEL` | Gemini model to use | `gemini-1.5-pro` |
| `CLOUD_RUN_REGION` | Region for Cloud Run | `us-central1` |

### Restricting Access

To require authentication, update the Terraform:

```hcl
# In terraform/main.tf, change this:
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  ...
  member   = "allUsers"  # Change to specific users/groups
}
```

Or use IAM:

```bash
# Remove public access
gcloud run services remove-iam-policy-binding delivery-finance-app \
    --region=$REGION \
    --member="allUsers" \
    --role="roles/run.invoker"

# Add specific users
gcloud run services add-iam-policy-binding delivery-finance-app \
    --region=$REGION \
    --member="user:email@example.com" \
    --role="roles/run.invoker"
```

## Files Structure

```
meddoc-intake-connector/
├── app/
│   ├── main.py              # Streamlit application
│   ├── universal_etl.py     # ETL processing logic
│   ├── requirements.txt     # Python dependencies
│   ├── Dockerfile           # Container definition
│   ├── cloudbuild.yaml      # Cloud Build config
│   └── .dockerignore        # Docker ignore rules
├── terraform/
│   └── main.tf              # Infrastructure as code
├── universal_etl.py         # Standalone ETL script
├── extract_schema.py        # Schema extraction tool
└── DEPLOYMENT.md            # This file
```

## Troubleshooting

### BigQuery Connection Issues

```bash
# Verify service account has correct permissions
gcloud projects get-iam-policy $PROJECT_ID \
    --flatten="bindings[].members" \
    --filter="bindings.members:delivery-finance-app"
```

### Vertex AI / Gemini Issues

```bash
# Ensure Vertex AI API is enabled
gcloud services enable aiplatform.googleapis.com

# Check quota
gcloud compute project-info describe --project $PROJECT_ID
```

### Container Startup Issues

```bash
# View logs
gcloud run services logs read delivery-finance-app --region $REGION --limit 50
```

## Cost Considerations

- **Cloud Run**: Pay per request, scales to zero
- **BigQuery**: First 1TB/month free, then $5/TB
- **Vertex AI (Gemini)**: ~$0.00025/1K input tokens, ~$0.0005/1K output tokens
- **Artifact Registry**: ~$0.10/GB/month

Estimated monthly cost for light usage: $10-50/month

## Next Steps

1. **Add authentication** - Integrate with Identity-Aware Proxy (IAP)
2. **Enable vector search** - Use BigQuery ML for semantic project search
3. **Set up CI/CD** - Connect Cloud Build to your Git repository
4. **Add monitoring** - Set up Cloud Monitoring dashboards and alerts
