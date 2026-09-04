# Deployment Guide: Backend (Render) + Frontend (Vercel)

This guide covers the complete CI/CD pipeline and deployment procedures for the Recoup autonomous receivables system.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Backend Deployment (Render)](#backend-deployment-render)
3. [Frontend Deployment (Vercel)](#frontend-deployment-vercel)
4. [CI/CD Pipeline](#cicd-pipeline)
5. [Environment Configuration](#environment-configuration)
6. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     CI/CD Pipeline (GitHub Actions)             │
├─────────────────────────────────────────────────────────────────┤
│ 1. Lint Backend (ruff)      │ 1. Lint Frontend (ESLint)         │
│ 2. Secret Scan (gitleaks)   │ 2. Build Frontend (Next.js)       │
│ 3. Type Check (mypy)        │                                   │
│ 4. Test Backend (pytest)    │                                   │
│ 5. Build Backend (wheel)    │                                   │
├─────────────────────────────────────────────────────────────────┤
│ On main branch push:                                            │
│ • Deploy Backend → Render (recoup-api)                          │
│ • Deploy Frontend → Vercel (recoup dashboard)                   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Points
- **Backend**: Python FastAPI, PostgreSQL, runs on Render with Uvicorn
- **Frontend**: Next.js React, deployed to Vercel for global edge network
- **Database**: PostgreSQL 15 on Render (managed Postgres with PITR)
- **Scheduler**: GitHub Actions (15-min interval) OR Render Cron
- **Secrets**: Environment-specific, managed per platform

---

## Backend Deployment (Render)

### Initial Setup

1. **Create Render Service**
   - Visit https://render.com
   - Connect your GitHub repository
   - Select "New +" → "Web Service"
   - Configure:
     - **Name**: recoup-api
     - **Repository**: this project
     - **Branch**: main
     - **Runtime**: Docker
     - **Build Command**: Uses Dockerfile (automatic)
     - **Start Command**: Uses Dockerfile CMD
     - **Plan**: Starter (or higher for production)

2. **Create Render PostgreSQL Database**
   - Select "New +" → "PostgreSQL"
   - Configure:
     - **Name**: recoup-db
     - **Plan**: Starter (or higher)
     - **Version**: 15
   - Copy connection string, will be used as DATABASE_URL

3. **Configure Environment Variables**
   ```
   APP_ENV=production
   DEBUG=false
   DRY_RUN=true              # Set to false when ready to send real email
   DATABASE_URL=<from-postgres>
   DB_POOL_MODE=null         # Use Render's pooler
   TASK_API_KEY=<generate-strong-random>
   RATE_LIMIT_REDIS_URL=<optional-redis-url>
   OTEL_EXPORTER_OTLP_ENDPOINT=<optional-grafana-cloud-url>
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <token>
   RAZORPAY_KEY_ID=rzp_live_xxxxx
   RAZORPAY_KEY_SECRET=xxxxx
   RAZORPAY_WEBHOOK_SECRET=xxxxx
   RESEND_API_KEY=re_xxxxx
   RESEND_FROM_EMAIL=noreply@yourdomain.com
   RESEND_WEBHOOK_SECRET=whsec_xxxxx
   REPLY_INBOUND_DOMAIN=reply.yourdomain.com
   REPLY_ADDRESS_SECRET=<generate-strong-random>
   CORS_ORIGINS=https://recoup.vercel.app,https://yourdomain.com
   ```

4. **Enable Auto-Deploy**
   - Render dashboard → Settings → Auto-Deploy = "Yes"
   - Next push to main triggers automatic deployment

5. **Set Up GitHub Deployment Hook**
   - Render dashboard → Settings → Deploy Hook URL
   - Copy the hook URL
   - Add to GitHub Secrets:
     - Go to repo → Settings → Secrets and variables → Actions
     - Add: `RENDER_DEPLOY_HOOK_URL=<hook-url>`

### Deployment Process

The backend automatically deploys on every push to main:
1. GitHub Actions runs CI pipeline (lint, test, build)
2. On success, triggers Render deployment hook
3. Render pulls latest code and rebuilds Docker image
4. Runs migration: `alembic upgrade head`
5. Starts new Uvicorn process
6. Health check verifies `/api/v1/health` returns 200

### Monitoring

```bash
# Check deployment status
curl https://recoup-api.onrender.com/api/v1/health

# View logs
# Render dashboard → Services → recoup-api → Logs tab
```

---

## Frontend Deployment (Vercel)

### Initial Setup

1. **Create Vercel Project**
   - Visit https://vercel.com
   - Click "Add New..." → "Project"
   - Import your GitHub repository
   - Select "Next.js" framework (auto-detected)
   - Configure:
     - **Root Directory**: ./frontend
     - **Build Command**: npm run build
     - **Output Directory**: .next

2. **Configure Environment Variables**
   - Vercel dashboard → Settings → Environment Variables
   - Add for all environments (Production, Preview, Development):
     ```
     NEXT_PUBLIC_API_URL=https://recoup-api.onrender.com/api/v1
     NEXT_PUBLIC_TASK_API_KEY=<from-backend-TASK_API_KEY>
     ```

3. **Domain Configuration (Optional)**
   - Add custom domain: Vercel dashboard → Settings → Domains
   - Examples:
     - `dashboard.yourdomain.com`
     - `app.yourdomain.com`

4. **Set Up GitHub Deployment Token**
   - GitHub → Personal access tokens → Tokens (classic)
   - Create with scopes: `repo`, `read:user`
   - Add to GitHub Secrets:
     - `VERCEL_TOKEN=<token>`
     - `VERCEL_API_URL=https://recoup-api.onrender.com/api/v1`
     - `VERCEL_TASK_API_KEY=<from-backend>`

### Deployment Process

The frontend automatically deploys on every push to main:
1. GitHub Actions builds Next.js app
2. On success, deploys to Vercel using `vercel deploy --prod`
3. Vercel builds and optimizes Next.js app
4. Deploys to global edge network
5. Preview URL available during build

### Manual Deployment

```bash
# From local machine (install Vercel CLI first)
npm i -g vercel

# Deploy to production
vercel deploy --prod --token $VERCEL_TOKEN

# Deploy to preview (default)
vercel deploy --token $VERCEL_TOKEN
```

### Monitoring

```bash
# Check deployment
curl https://recoup.vercel.app/api/health

# View logs
# Vercel dashboard → Deployments → select deployment → Logs tab
```

---

## CI/CD Pipeline

### Workflow: `.github/workflows/ci-cd.yml`

Runs on every push to `main` or `develop`, and on all pull requests.

#### Stage 1: Lint & Scan

```
lint-backend ──┐
lint-frontend ─┤
secret-scan ───┘─→ [continue if all pass]
```

- **lint-backend**: Ruff format + import checking
- **lint-frontend**: ESLint for React/TypeScript
- **secret-scan**: Gitleaks detects exposed secrets

#### Stage 2: Quality Checks

```
typecheck ───┐
test-backend ┤─→ [continue if all pass]
              └─
```

- **typecheck**: mypy type checking (Python)
- **test-backend**: pytest with PostgreSQL + Redis

#### Stage 3: Build

```
build-backend ───┐
build-frontend ──┤─→ [continue if both pass]
                  └─
```

- **build-backend**: Python wheel package
- **build-frontend**: Next.js static export/bundle

#### Stage 4: Deploy (main branch only)

```
deploy-backend ──┐
deploy-frontend ─┤─→ [on main push only]
                  └─
```

- **deploy-backend**: Render hook trigger
- **deploy-frontend**: Vercel deployment

### GitHub Secrets Required

For the pipeline to work, configure these in repository Settings → Secrets:

```yaml
# Backend deployment
RENDER_DEPLOY_HOOK_URL=https://api.render.com/deploy/srv-...

# Frontend deployment  
VERCEL_TOKEN=<personal-access-token>
VERCEL_API_URL=https://recoup-api.onrender.com/api/v1
VERCEL_TASK_API_KEY=<same-as-backend-TASK_API_KEY>

# Infrastructure (optional, used in observability)
OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64-token>
```

### Workflow Status

- ✅ All jobs pass → Deployment proceeds
- ❌ Any job fails → Deployment blocked, email notification sent
- ⚠️ PRs use full pipeline but skip deployment jobs

---

## Environment Configuration

### Backend Environment Variables

```bash
# Application
APP_ENV=production              # or development
DEBUG=false                     # disable /docs in production
DRY_RUN=false                   # set to true to render emails without sending

# Database
DATABASE_URL=postgresql+psycopg://user:pass@host/dbname
DB_POOL_MODE=null               # use managed pooler (Neon, Render)
DB_POOL_SIZE=5                  # connection pool size (if not null)

# Authentication
TASK_API_KEY=<strong-random-string-32-chars>
API_KEY=<strong-random-string-32-chars>

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_REDIS_URL=redis://user:pass@host:port/db

# Payment Processing
RAZORPAY_KEY_ID=rzp_live_xxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=whsec_xxxxxxxxxx

# Email Delivery
RESEND_API_KEY=re_xxxxxxxxxx
RESEND_FROM_EMAIL=noreply@yourdomain.com
RESEND_WEBHOOK_SECRET=whsec_xxxxxxxxxx

# Reply Routing
REPLY_INBOUND_DOMAIN=reply.yourdomain.com
REPLY_ADDRESS_SECRET=<strong-random-string>

# AI/LLM
GROQ_API_KEY=gsk_xxxxx (optional)
GEMINI_API_KEY=xxxxx (optional)

# Observability
OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo.grafana.net/otlp (optional)
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic xxxxx (optional)
OTEL_SERVICE_NAME=recoup

# CORS
CORS_ORIGINS=https://dashboard.yourdomain.com,https://app.yourdomain.com
```

### Frontend Environment Variables

```bash
# Public (visible in browser)
NEXT_PUBLIC_API_URL=https://recoup-api.onrender.com/api/v1
NEXT_PUBLIC_TASK_API_KEY=<from-backend-TASK_API_KEY>
```

### Local Development

```bash
# Backend: create .env file
cp .env.example .env
# Edit .env with local values

# Frontend: create .env.local file
cd frontend
cp .env.local.example .env.local
# Edit .env.local with local values

# Run both
python -m uvicorn app.main:app --reload      # Backend
npm run dev                                   # Frontend (from frontend/ dir)
```

---

## Troubleshooting

### Backend Deployment Issues

#### 1. Build Fails with "DATABASE_URL not set"

**Cause**: Environment variable not configured before first deployment

**Fix**:
```bash
# Set in Render dashboard before triggering deploy
# Services → recoup-api → Environment → add DATABASE_URL
# Then manually trigger deploy via Render dashboard
```

#### 2. Migration Fails During Deployment

**Cause**: Schema mismatch or migration dependency issue

**Fix**:
```bash
# SSH into Render container and debug
render logs recoup-api
render shell recoup-api
alembic history
alembic current
```

#### 3. Health Check Fails (503 Service Unavailable)

**Cause**: App can't reach database or secrets are invalid

**Fix**:
```bash
# Check logs
curl https://recoup-api.onrender.com/api/v1/health -v
# Should see detailed error message

# Verify secrets
# Check DATABASE_URL, TASK_API_KEY, etc. in Render environment
```

### Frontend Deployment Issues

#### 1. Build Fails "Cannot find module"

**Cause**: Dependency issue or missing build step

**Fix**:
```bash
# Rebuild locally to test
cd frontend
npm install --legacy-peer-deps
npm run build

# Check for errors, commit fix
git add package-lock.json
git commit -m "fix: rebuild dependencies"
```

#### 2. "NEXT_PUBLIC_API_URL not set"

**Cause**: Environment variable missing in Vercel

**Fix**:
```bash
# Add to Vercel: Settings → Environment Variables
NEXT_PUBLIC_API_URL=https://recoup-api.onrender.com/api/v1
# Redeploy: Vercel dashboard → Deployments → Redeploy
```

#### 3. CORS Errors in Browser Console

**Cause**: Frontend origin not in backend CORS_ORIGINS

**Fix**:
```bash
# Get Vercel domain (e.g., recoup.vercel.app or custom)
# Add to backend environment variable:
CORS_ORIGINS=https://recoup.vercel.app,https://yourdomain.com
# Redeploy backend on Render
```

### GitHub Actions Workflow Issues

#### 1. Tests Pass Locally but Fail in CI

**Cause**: CI environment difference (PostgreSQL version, Redis, etc.)

**Fix**:
```bash
# Check CI environment in .github/workflows/ci-cd.yml
# Verify PostgreSQL: 15, Redis: 7
# Verify Python: 3.11

# Run locally in similar environment
docker run --rm -it \
  -v $(pwd):/app \
  -e POSTGRES_URL=postgresql://test:test@localhost/test \
  python:3.11 bash
```

#### 2. Secret Scan Fails with False Positive

**Cause**: Actual secret or pattern match on example values

**Fix**:
```bash
# Option 1: Use .gitignore or .gitattributes to exclude file
# Option 2: Mark as false positive in gitleaks config
# Option 3: Use placeholder values in examples
```

### Monitoring & Observability

```bash
# Backend health
curl https://recoup-api.onrender.com/api/v1/ready

# Frontend connectivity
curl https://recoup.vercel.app/

# Check logs
Render: dashboard → Services → recoup-api → Logs
Vercel: dashboard → Deployments → [latest] → Logs

# Check observability traces (if configured)
Grafana Cloud → Explore → select recoup service
```

---

## Rollback Procedures

### Render Backend

```bash
# Option 1: Redeploy previous commit
git revert <bad-commit-hash>
git push origin main  # Auto-triggers deployment

# Option 2: Manual rollback in Render
# Dashboard → Services → recoup-api → Deploys
# Click previous successful deploy → "Deploy"
```

### Vercel Frontend

```bash
# Option 1: Redeploy previous commit
git revert <bad-commit-hash>
git push origin main  # Auto-triggers deployment

# Option 2: Manual rollback in Vercel
# Dashboard → Deployments → [previous] → three-dots → "Promote to Production"
```

---

## Best Practices

1. **Always test locally first**
   ```bash
   npm run build  # Frontend
   pytest         # Backend
   ```

2. **Use feature branches**
   ```bash
   git checkout -b feature/my-feature
   # Test in PR preview (Vercel)
   # Merge only after CI passes
   ```

3. **Monitor deployments**
   - Set up Slack/email notifications in GitHub + Vercel
   - Check health endpoints after each deploy
   - Review logs for warnings

4. **Rotate secrets regularly**
   - TASK_API_KEY: quarterly
   - Razorpay keys: annually or on compromise
   - Follow secret rotation runbook

5. **Use preview deployments**
   - PRs automatically get preview Vercel deployments
   - Test integration before merging to main

---

## Support

For issues or questions:
1. Check logs: Render dashboard + Vercel dashboard
2. Run health checks: `/api/v1/health`, `/api/v1/ready`
3. Review GitHub Actions run details
4. Check observability traces (if configured with Grafana)

