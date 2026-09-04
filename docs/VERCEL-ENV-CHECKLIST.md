# ✅ Vercel Environment Setup Checklist

## Status: Fix Applied & Ready

**Issue**: Pydantic validation errors on Vercel deployment  
**Root Cause**: Empty environment variables  
**Solution**: Added env_ignore_empty=True to config  
**Status**: ✅ FIXED - Ready for deployment  

---

## ✅ VERCEL ENVIRONMENT VARIABLES SETUP

### Step 1: Access Vercel Environment Settings

1. Go to: https://vercel.com/dashboard
2. Select your project: recoup
3. Click: Settings
4. Click: Environment Variables (left sidebar)

### Step 2: Add Environment Variables

For each variable below, click "Add":
- Variable Name: (exact name)
- Value: (from your services)
- Select Environments: Check ALL (Production, Preview, Development)
- Click "Add"

### Step 3: Configure Variables

#### CRITICAL - Must Have (App won't start without these)

- [ ] **DATABASE_URL**
  - Value: \postgresql://user:pass@host:port/dbname\
  - Environments: All
  - Get from: PostgreSQL provider (Render, Neon, etc.)

- [ ] **APP_ENV**
  - Value: \production\
  - Environments: All
  - Note: This triggers validation checks

#### HIGHLY RECOMMENDED - Set These

- [ ] **DEBUG**
  - Value: \alse\
  - Environments: All
  - Note: If not set, won't show /docs

- [ ] **DRY_RUN**
  - Value: \alse\
  - Environments: All (or true for testing)
  - Note: false = send real emails, true = simulation

- [ ] **TASK_API_KEY**
  - Value: Generate strong 32-character random string
  - Environments: All
  - Note: Protects /api/v1/tasks/run-batch endpoint
  - Generate: \$(python -c "import secrets; print(secrets.token_hex(16))")\

- [ ] **RAZORPAY_KEY_ID**
  - Value: \zp_live_xxxxxxxxxx\ (from Razorpay)
  - Environments: All
  - Get from: Razorpay Dashboard → Settings → API Keys

- [ ] **RAZORPAY_KEY_SECRET**
  - Value: (Razorpay secret)
  - Environments: All

- [ ] **RAZORPAY_WEBHOOK_SECRET**
  - Value: (webhook signing secret from Razorpay)
  - Environments: All

- [ ] **RESEND_API_KEY**
  - Value: \e_xxxxxxxxxx\ (from Resend)
  - Environments: All
  - Get from: Resend Dashboard → API Keys

- [ ] **RESEND_FROM_EMAIL**
  - Value: \
oreply@yourdomain.com\
  - Environments: All
  - Note: Must be verified in Resend

#### RECOMMENDED - For Full Functionality

- [ ] **CORS_ORIGINS**
  - Value: \https://yourdomain.com,https://app.yourdomain.com\
  - Environments: All
  - Note: Frontend domains that can call this API

- [ ] **DB_POOL_MODE**
  - Value: \default\ or \
ull\ (null for PgBouncer)
  - Environments: All
  - Default: default

- [ ] **RATE_LIMIT_PER_MINUTE**
  - Value: \60\
  - Environments: All
  - Default: 60

- [ ] **BATCH_MAX_INVOICES**
  - Value: \1920\
  - Environments: All
  - Default: 1920

- [ ] **SENDING_ENABLED**
  - Value: \	rue\ or \alse\
  - Environments: All
  - Default: true (false = kill switch)

#### OPTIONAL - Advanced (Use Defaults if Blank)

- [ ] REPLY_INBOUND_DOMAIN
- [ ] REPLY_ADDRESS_SECRET
- [ ] GROQ_API_KEY
- [ ] GEMINI_API_KEY
- [ ] RATE_LIMIT_REDIS_URL
- [ ] OTEL_EXPORTER_OTLP_ENDPOINT
- [ ] OTEL_EXPORTER_OTLP_HEADERS

---

## ✅ VERIFICATION STEPS

### Step 1: Verify Variables Added

In Vercel dashboard:
- [ ] All variables showing in Environment Variables list
- [ ] All environments selected (3 checkmarks per variable)
- [ ] No typos in variable names

### Step 2: Deploy with New Variables

1. [ ] Push code to main branch:
   \\\ash
   git add app/core/config.py docs/
   git commit -m "fix: add env_ignore_empty for Vercel deployment"
   git push origin main
   \\\

2. [ ] Watch Vercel deployment:
   - Go to: Vercel Dashboard → Deployments
   - Should show "Building..."
   - Then "Ready"
   - Should NOT show build errors

### Step 3: Test Health Endpoint

After deployment completes:

\\\ash
# Check if app is healthy
curl https://your-app.vercel.app/api/v1/health

# Expected response:
{
  "status": "healthy",
  "service": "Recoup",
  "environment": "production",
  "version": "0.1.0"
}
\\\

- [ ] Health endpoint returns 200
- [ ] Status is "healthy"
- [ ] Environment is "production"

### Step 4: Check Detailed Readiness

\\\ash
# Comprehensive readiness check
curl https://your-app.vercel.app/api/v1/ready

# Expected response includes all checks:
{
  "status": "ready",
  "checks": {
    "db": "ok",
    "redis": "skipped"  # (or "ok" if RATE_LIMIT_REDIS_URL set)
  }
}
\\\

- [ ] Ready endpoint returns 200
- [ ] All checks pass or skipped
- [ ] Database connection works

### Step 5: Monitor Logs

In Vercel Dashboard:
- [ ] Click: Deployments → [latest]
- [ ] Click: Logs tab
- [ ] Look for:
  - "Starting application"
  - "Database reachable"
  - NO "ValidationError" messages
  - NO "ImportError" messages

---

## 🚀 IF DEPLOYMENT STILL FAILS

### Check 1: Verify Fix Was Deployed

1. Vercel Dashboard → Deployments → [latest] → Logs
2. Look for: \env_ignore_empty=True\ in logs
3. If not there: code fix not deployed yet
   - Wait for automatic redeploy after push
   - Or click "Redeploy" button

### Check 2: Verify DATABASE_URL

1. Is DATABASE_URL set in Vercel environment?
2. Is it valid PostgreSQL connection string?
3. Format: \postgresql://user:pass@host:port/dbname\

### Check 3: Verify APP_ENV

1. Is APP_ENV set to "production"?
2. Check exact value (case-sensitive)

### Check 4: Force Redeploy with New Variables

1. Vercel Dashboard → Deployments → [latest]
2. Click three dots → "Redeploy"
3. Confirm redeploy
4. Wait for build to complete

### Check 5: Check for Secrets Validation

If seeing "placeholder values" error:
1. TASK_API_KEY is empty or "change-me"
2. GROQ_API_KEY and GEMINI_API_KEY both empty
3. Razorpay/Resend keys still have defaults

Fix by setting real values in Vercel environment

---

## ✅ FINAL VERIFICATION

Once deployment succeeds:

- [ ] Health endpoint: https://your-app.vercel.app/api/v1/health
- [ ] Ready endpoint: https://your-app.vercel.app/api/v1/ready
- [ ] No errors in Vercel logs
- [ ] Environment shows "production"
- [ ] Database connection works

### Then Test Integration

- [ ] Frontend can reach backend
- [ ] API calls from frontend succeed
- [ ] No CORS errors in browser

---

## 📋 ENVIRONMENT VARIABLES QUICK REFERENCE

| Variable | Required | Default | Example |
|----------|----------|---------|---------|
| DATABASE_URL | ✅ | none | postgresql://... |
| APP_ENV | ✅ | development | production |
| DEBUG | ⚠️ | false | false |
| DRY_RUN | ⚠️ | true | false |
| TASK_API_KEY | ⚠️ | empty | (32-char random) |
| RAZORPAY_KEY_ID | ⚠️ | placeholder | rzp_live_... |
| RAZORPAY_KEY_SECRET | ⚠️ | placeholder | (from dashboard) |
| RESEND_API_KEY | ⚠️ | placeholder | re_... |
| RESEND_FROM_EMAIL | ⚠️ | placeholder | noreply@domain |
| RATE_LIMIT_PER_MINUTE | ⚠️ | 60 | 60 |
| BATCH_MAX_INVOICES | ⚠️ | 1920 | 1920 |
| CORS_ORIGINS | ⚠️ | empty | https://domain |
| RATE_LIMIT_REDIS_URL | ❌ | none | redis://... |
| OTEL_EXPORTER_OTLP_ENDPOINT | ❌ | none | https://... |

✅ = Required  
⚠️ = Recommended (app degrades if not set)  
❌ = Optional (defaults used)  

---

**Status**: ✅ Ready for Vercel Environment Setup  
**Last Updated**: September 4, 2026  
**Fix Applied**: env_ignore_empty=True in config.py  

