# Recoup Cloudflare Email Worker

An edge Cloudflare Email Routing Worker that receives inbound customer email replies, parses raw RFC 5322 MIME messages using `postal-mime`, signs them with Svix-compatible HMAC-SHA256 signatures, and forwards the structured reply to Recoup's Promise-to-Pay ingestion endpoint (`POST /api/v1/replies`).

---

## 🚀 Cloudflare Dashboard Deployment (Git Integration)

When connecting your GitHub repository to Cloudflare Workers in the dashboard:

| Setting | Value |
| :--- | :--- |
| **Root directory** | `cloudflare-email-worker` |
| **Build command** | `npm install` *(or `npm run build`)* |
| **Deploy command** | `npx wrangler deploy` |

### Environment Variables & Secrets in Cloudflare Dashboard:
In Cloudflare Dashboard -> **Workers & Pages** -> your worker -> **Settings** -> **Variables and Secrets**:
1. **`BACKEND_URL`** (Type: Plain text variable):
   - Your public backend URL, e.g. `https://xxxx.ngrok-free.app` or `https://api.yourdomain.xyz`.
   - *Do not include a trailing slash.*
2. **`WEBHOOK_SECRET`** (Type: Encrypted Secret):
   - Value of `RESEND_WEBHOOK_SECRET` from your backend `.env` (e.g. `whsec_...`).

---

## 💻 Manual CLI Deployment (Wrangler)

If deploying directly from your machine:

```bash
cd cloudflare-email-worker

# 1. Install dependencies
npm install

# 2. Add your secret (matches RESEND_WEBHOOK_SECRET in backend .env)
npx wrangler secret put WEBHOOK_SECRET

# 3. Deploy to Cloudflare Workers
npx wrangler deploy
```

---

## 📬 Cloudflare Email Routing Setup (Step-by-Step)

1. **Activate Email Routing on your domain**:
   - Go to Cloudflare Dashboard -> select your domain (e.g. `yourdomain.xyz`).
   - Click **Email** -> **Email Routing**.
   - Follow Cloudflare's 1-click wizard to automatically configure the DNS **MX** and **SPF** records.

2. **Add a Routing Rule to invoke this worker**:
   - In **Email Routing** -> **Routing Rules** tab:
   - Click **Create rule**.
   - Custom address: `reply` or `Catch-all` (Catch-all is recommended so all `reply+<invoice>.<tag>@yourdomain.xyz` sub-addresses route here).
   - **Action**: Select **Send to a Worker**.
   - **Destination**: Select `recoup-email-worker`.
   - Click **Save**.

3. **Configure Backend Settings (`.env`)**:
   - Set `REPLY_INBOUND_DOMAIN` in your backend `.env`:
     ```env
     REPLY_INBOUND_DOMAIN=yourdomain.xyz
     ```
   - When the Recoup agent sends an overdue reminder via Resend, it automatically injects:
     `Reply-To: reply+INV-2026-00042.<hmac_tag>@yourdomain.xyz`
   - When the recipient clicks "Reply" in their email client and sends "We will pay this by Friday":
     1. Cloudflare receives it on MX.
     2. Cloudflare triggers `recoup-email-worker`.
     3. Worker parses email text and signs the payload.
     4. Worker delivers to `POST /api/v1/replies`.
     5. Backend verifies signature, resolves `INV-2026-00042`, classifies `PROMISE_TO_PAY`, and updates the invoice to `PROMISED`.

---

## 🧪 Testing & Verification

### A. Health Check
```bash
curl -i https://recoup-email-worker.<your-subdomain>.workers.dev/health
```

### B. Instant Email Simulation (No DNS wait required!)
The worker includes a `/test-simulate` endpoint so you can verify the entire pipeline before DNS propagates:

```bash
curl -X POST https://recoup-email-worker.<your-subdomain>.workers.dev/test-simulate \
  -H "Content-Type: application/json" \
  -d '{
    "from": "customer@example.com",
    "to": ["reply+INV-2026-00042.8a2b3c4d5e6f7a8b@yourdomain.xyz"],
    "subject": "Re: Overdue Invoice",
    "text": "We will clear this invoice by Friday"
  }'
```

### C. Live Log Streaming
```bash
npx wrangler tail
```
