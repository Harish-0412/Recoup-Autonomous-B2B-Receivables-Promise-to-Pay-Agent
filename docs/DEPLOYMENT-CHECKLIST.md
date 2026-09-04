# ✅ CI/CD & Deployment Checklist

## Pre-Deployment Setup

### Local Testing
- [ ] Run \pytest\ - all tests pass
- [ ] Run \mypy app/\ - no type errors
- [ ] Run \uff check .\ - no lint errors
- [ ] Run \uff format --check .\ - code formatted
- [ ] Run \cd frontend && npm run build\ - frontend builds
- [ ] Review changes: \git status\

### Git Preparation
- [ ] Create feature branch: \git checkout -b chore/deploy-setup\
- [ ] Stage files: \git add .\
- [ ] Commit: \git commit -m "chore: add CI/CD pipeline and Vercel deployment config"\
- [ ] Push: \git push origin chore/deploy-setup\
- [ ] Create PR on GitHub

### PR Review
- [ ] CI pipeline runs (check GitHub Actions)
- [ ] All jobs pass (lint, test, build)
- [ ] Review code changes
- [ ] Merge PR when ready

### Merge to Main
- [ ] Switch to main: \git checkout main\
- [ ] Pull latest: \git pull origin main\
- [ ] Merge PR: \git merge chore/deploy-setup\
- [ ] Push: \git push origin main\
- [ ] ⚠️ **This triggers CI/CD and deployment!**

---

## Infrastructure Setup (Render Backend)

### Create Render Service
- [ ] Go to https://render.com
- [ ] Click "New +" → "Web Service"
- [ ] Select your GitHub repo
- [ ] Configure:
  - [ ] Name: recoup-api
  - [ ] Runtime: Docker
  - [ ] Branch: main
  - [ ] Plan: Starter (or higher)

### Create PostgreSQL Database
- [ ] Click "New +" → "PostgreSQL"
- [ ] Configure:
  - [ ] Name: recoup-db
  - [ ] Version: 15
  - [ ] Plan: Starter
- [ ] Copy PostgreSQL connection string

### Environment Variables (Render)
- [ ] Set DATABASE_URL to PostgreSQL connection string
- [ ] Set APP_ENV=production
- [ ] Set DEBUG=false
- [ ] Set DRY_RUN=false (when ready to send real email)
- [ ] Generate and set TASK_API_KEY (strong random string)
- [ ] Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
- [ ] Set RAZORPAY_WEBHOOK_SECRET
- [ ] Set RESEND_API_KEY
- [ ] Set RESEND_FROM_EMAIL
- [ ] Set RESEND_WEBHOOK_SECRET
- [ ] Set REPLY_INBOUND_DOMAIN
- [ ] Set REPLY_ADDRESS_SECRET
- [ ] Set CORS_ORIGINS (add Vercel domain later)
- [ ] (Optional) Set OTEL_EXPORTER_OTLP_ENDPOINT for observability
- [ ] (Optional) Set RATE_LIMIT_REDIS_URL

### Enable Auto-Deploy
- [ ] Render Settings → Auto-Deploy = Yes
- [ ] Next push to main will auto-deploy

### Get Deploy Hook URL
- [ ] Services → recoup-api → Settings → Scroll to Deploy Hook
- [ ] Copy the deploy hook URL

---

## GitHub Secrets Configuration

### Add Secrets for CI/CD
Go to repo: **Settings → Secrets and variables → Actions**

- [ ] Add RENDER_DEPLOY_HOOK_URL
  - Value: (from Render dashboard)
  - Purpose: Trigger backend deployment

- [ ] Create Vercel Personal Token
  - Go to https://vercel.com → Account → Tokens
  - Create new token (scope: all)
  - [ ] Add VERCEL_TOKEN to GitHub Secrets

- [ ] Add VERCEL_API_URL to GitHub Secrets
  - Value: https://recoup-api.onrender.com/api/v1
  - Purpose: Tell frontend where backend is

- [ ] Add VERCEL_TASK_API_KEY to GitHub Secrets
  - Value: (same as backend TASK_API_KEY)
  - Purpose: Frontend auth for API calls

---

## Infrastructure Setup (Vercel Frontend)

### Create Vercel Project
- [ ] Go to https://vercel.com
- [ ] Click "Add New..." → "Project"
- [ ] Import your GitHub repository
- [ ] Configure:
  - [ ] Framework: Next.js (auto-detected)
  - [ ] Root Directory: ./frontend
  - [ ] Build Command: npm run build
  - [ ] Install Command: npm install

### Environment Variables (Vercel)
- [ ] Add NEXT_PUBLIC_API_URL
  - Value: https://recoup-api.onrender.com/api/v1
  - Environments: Production, Preview, Development
  - Save

- [ ] Add NEXT_PUBLIC_TASK_API_KEY
  - Value: (same as backend TASK_API_KEY)
  - Environments: Production, Preview, Development
  - Save

### Domain Configuration (Optional)
- [ ] Vercel Dashboard → Settings → Domains
- [ ] Add custom domain (e.g., dashboard.yourdomain.com)
- [ ] Update DNS records as instructed

### Connect to GitHub
- [ ] Vercel should auto-connect to GitHub
- [ ] Next push to main will auto-deploy

---

## Testing Deployment

### Test Backend
- [ ] Wait for Render deployment to complete (~5 min)
- [ ] Check: https://recoup-api.onrender.com/api/v1/health
- [ ] Should return: \{"status": "healthy", ...}\
- [ ] Check logs in Render Dashboard for errors

### Test Frontend
- [ ] Wait for Vercel deployment to complete (~3 min)
- [ ] Check: https://recoup.vercel.app
- [ ] Should load dashboard without errors
- [ ] Check browser console (F12) for CORS errors

### Test Integration
- [ ] Frontend should connect to backend
- [ ] Check: Browser console for network requests
- [ ] All requests should succeed (no 403/401 errors)

### Manual Test Commands
\\\ash
# Backend health
curl https://recoup-api.onrender.com/api/v1/health

# Backend ready (full check)
curl https://recoup-api.onrender.com/api/v1/ready

# Frontend
curl https://recoup.vercel.app/

# Check logs
# Render: Dashboard → recoup-api → Logs
# Vercel: Dashboard → Deployments → [latest] → Logs
\\\

---

## Post-Deployment Configuration

### Register Webhooks
- [ ] Razorpay Dashboard: Add webhook
  - URL: https://recoup-api.onrender.com/api/v1/webhooks/razorpay
  - Events: payment.authorized, payment.failed
  - Token: (set RAZORPAY_WEBHOOK_SECRET in backend)

- [ ] Resend: Add webhook (if inbound replies enabled)
  - URL: https://recoup-api.onrender.com/api/v1/replies
  - Events: inbound_email

### Email Domain Setup
- [ ] Configure MX records for REPLY_INBOUND_DOMAIN
- [ ] Point to Resend's email servers
- [ ] Test: Send reply to reply+test@REPLY_INBOUND_DOMAIN

### Scheduler Configuration
Choose ONE method:

**Option A: GitHub Actions** (default)
- [ ] GitHub → Settings → Secrets
- [ ] Add RECOUP_API_URL = https://recoup-api.onrender.com
- [ ] Add TASK_API_KEY = (from backend environment)
- [ ] GitHub Actions runs every 15 minutes automatically

**Option B: Render Cron**
- [ ] Uncomment cron job in render.yaml
- [ ] Redeploy on Render
- [ ] Remove GitHub scheduler workflow (avoid double-fire)

---

## Verification Checklist

### API Endpoints
- [ ] GET /api/v1/health → 200 (health check)
- [ ] GET /api/v1/ready → 200 (full readiness)
- [ ] POST /api/v1/tasks/run-batch → 200 (scheduler trigger)
- [ ] GET /api/v1/invoices → 200 (requires auth)

### Database
- [ ] [ ] Check Render PostgreSQL connection works
- [ ] Run migrations completed: \lembic current\
- [ ] Database has tables: \SELECT COUNT(*) FROM information_schema.tables;\

### Frontend
- [ ] Dashboard loads without 404
- [ ] Can navigate between pages
- [ ] API calls succeed (check Network tab)
- [ ] No JavaScript errors in console

### Monitoring
- [ ] Set up Render monitoring (optional)
- [ ] Set up Vercel analytics (optional)
- [ ] Configure log aggregation (optional)

---

## Troubleshooting

### "API returns 503 Service Unavailable"
- [ ] Check Render logs for startup errors
- [ ] Verify DATABASE_URL is set
- [ ] Verify TASK_API_KEY is set
- [ ] Check alembic migrations ran: \lembic history\

### "Frontend won't load / blank page"
- [ ] Check browser console for errors (F12)
- [ ] Check NEXT_PUBLIC_API_URL matches backend URL
- [ ] Check Vercel deployment logs
- [ ] Clear browser cache and reload

### "CORS errors in browser"
- [ ] Backend: Check CORS_ORIGINS includes Vercel domain
- [ ] Backend: Restart/redeploy if changed
- [ ] Frontend: Check API_URL matches backend exactly
- [ ] Check both http/https protocols match

### "CI pipeline failed"
- [ ] Check GitHub Actions logs for specific error
- [ ] Common fixes:
  - Run \uff format .\ locally
  - Run \pytest\ to check tests
  - Run \mypy app/\ to check types
- [ ] Push fixes and try again

---

## Final Steps

- [ ] Document any custom configurations made
- [ ] Share deployment URLs with team
- [ ] Update DNS records if using custom domains
- [ ] Set up monitoring alerts
- [ ] Schedule regular backups (Render PITR enabled by default)
- [ ] Test rollback procedure:
  - [ ] Make a change
  - [ ] Deploy
  - [ ] Rollback via Render/Vercel dashboard
  - [ ] Verify rollback works

---

## 🎉 Success Criteria

When ALL of these are true, deployment is complete:

✅ CI pipeline passes on every push  
✅ Backend deploys automatically to Render  
✅ Frontend deploys automatically to Vercel  
✅ Health check endpoints return 200  
✅ Frontend can call backend API  
✅ No errors in logs  
✅ Webhooks registered and working  
✅ Scheduler runs every 15 minutes  

---

## 📞 Quick Reference

| Component | Dashboard | Status Check |
|-----------|-----------|--------------|
| GitHub Actions | https://github.com/repo/actions | Repo → Actions tab |
| Backend (Render) | https://dashboard.render.com | Services → recoup-api → Logs |
| Frontend (Vercel) | https://vercel.com | Deployments → [latest] |
| PostgreSQL (Render) | https://dashboard.render.com | Databases → recoup-db |
| API Health | https://recoup-api.onrender.com/api/v1/health | Should return 200 |
| Frontend | https://recoup.vercel.app | Should load dashboard |

---

**Document Updated**: 2026-09-04  
**Status**: Ready for Deployment  
**Next**: Follow checklist top-to-bottom for complete setup  

