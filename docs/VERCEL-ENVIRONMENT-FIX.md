# ✅ Vercel Deployment Fix: Environment Configuration

## Problem Identified

The deployment failed with pydantic validation errors when Vercel set empty environment variables:

\\\
ValidationError: 9 validation errors for Settings
DEBUG - Input should be a valid boolean, unable to interpret input 
DB_POOL_MODE - Input should be 'default' or 'null'
DRY_RUN - Input should be a valid boolean
... (more validation errors)
\\\

**Root Cause**: Vercel was setting empty strings for unconfigured environment variables, causing pydantic to fail parsing boolean/integer fields.

## Solution Applied

### Fix 1: Updated Configuration (app/core/config.py)
✅ Added \env_ignore_empty=True\ to SettingsConfigDict

This tells pydantic-settings to treat empty environment variables as "not provided" and use the Field defaults instead.

**Before**:
\\\python
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
)
\\\

**After**:
\\\python
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
    env_ignore_empty=True,  # ← FIX: Ignore empty strings
)
\\\

### Fix 2: Proper Vercel Environment Variables

Set these in your Vercel project (Settings → Environment Variables):

#### Critical (must set):
\\\
DATABASE_URL=postgresql://user:pass@host:5432/db
APP_ENV=production
\\\

#### Recommended (for full functionality):
\\\
DEBUG=false
DRY_RUN=false
TASK_API_KEY=<generate-strong-random-string-32-chars>
RAZORPAY_KEY_ID=rzp_live_xxxxx
RAZORPAY_KEY_SECRET=xxxxx
RESEND_API_KEY=re_xxxxx
RESEND_FROM_EMAIL=noreply@yourdomain.com
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
\\\

#### Optional (will use defaults if empty):
\\\
RATE_LIMIT_PER_MINUTE=60                    # default: 60
BATCH_MAX_INVOICES=1920                     # default: 1920
SCHEDULER_INTERVAL_SECONDS=300              # default: 300
SENDING_ENABLED=true                        # default: true
\\\

## Deployment Steps

1. **Add Environment Variables to Vercel**:
   - Go to: Vercel Dashboard → Settings → Environment Variables
   - For each environment (Production, Preview, Development):
     - Add all required variables
     - Leave optional variables empty (defaults will be used)

2. **Deploy**:
   - Push code to main
   - Vercel automatically redeploys
   - CI/CD runs with proper environment handling

3. **Verify**:
   - Check: https://your-app.vercel.app/api/v1/health
   - Should return 200 with healthy status
   - Check logs for any warnings

## Environment Variables Reference

### By Importance

**MUST SET**:
- DATABASE_URL - PostgreSQL connection string
- APP_ENV - Set to "production"

**SHOULD SET**:
- DEBUG - Set to false
- DRY_RUN - Set to false
- TASK_API_KEY - Scheduler authentication
- RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET - Payment processing
- RESEND_API_KEY - Email delivery

**NICE TO HAVE**:
- RATE_LIMIT_REDIS_URL - Shared rate limiting (optional)
- OTEL_EXPORTER_OTLP_ENDPOINT - Observability (optional)
- CORS_ORIGINS - Frontend domain (recommended)

**DEFAULTS USED IF EMPTY**:
- DEBUG: false
- DRY_RUN: true (safe default - won't send real emails)
- RATE_LIMIT_PER_MINUTE: 60
- BATCH_MAX_INVOICES: 1920
- DB_POOL_MODE: default
- DB_POOL_SIZE: 5

## Vercel Dashboard Configuration

### Quick Setup

1. **Project Settings**:
   - Settings → Environment Variables
   - Select "Production" environment

2. **Add Variables**:
   - Click "Add"
   - Enter: DATABASE_URL
   - Value: (from your PostgreSQL provider)
   - Scope: Production, Preview, Development
   - Click "Add"

3. **Repeat for**:
   - APP_ENV=production
   - DEBUG=false
   - DRY_RUN=false
   - TASK_API_KEY=<random-string>
   - All Razorpay keys
   - Resend keys
   - CORS_ORIGINS

4. **Deploy**:
   - Push changes to main
   - Vercel automatically redeploys

## Troubleshooting

### If still getting validation errors:

1. **Check Vercel Logs**:
   - Deployments → [latest] → Logs
   - Look for pydantic error messages

2. **Common Issues**:
   - DATABASE_URL empty → Set to valid PostgreSQL connection
   - APP_ENV not set → Set to "production"
   - Invalid JSON for complex types → Leave empty (use defaults)

3. **Force Redeploy**:
   - Vercel Dashboard → Deployments → [latest] → Redeploy

### If health check fails:

\\\ash
curl https://your-app.vercel.app/api/v1/health
\\\

Should return:
\\\json
{
  "status": "healthy",
  "service": "Recoup",
  "environment": "production",
  "version": "0.1.0"
}
\\\

If not:
- Check Vercel logs
- Verify DATABASE_URL is set
- Verify RAZORPAY/RESEND keys if APP_ENV=production

## What Changed

**Files Modified**:
- ✅ app/core/config.py - Added env_ignore_empty=True

**Why It Works Now**:
1. Vercel sets empty strings for unconfigured variables
2. SettingsConfigDict now ignores these empty values
3. Pydantic uses Field defaults instead
4. App boots successfully with partial configuration

**Migration from Old Config**:
- No breaking changes
- Existing deployments continue working
- New Vercel deployments now work correctly

## Next Steps

1. Update all environment variables in Vercel
2. Push code to main (includes the config fix)
3. Monitor first deployment
4. Verify health endpoints
5. Test integration with frontend

---

**Fix Applied**: September 4, 2026  
**Status**: ✅ Ready for Vercel Deployment

