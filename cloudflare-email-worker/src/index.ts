/**
 * Cloudflare Email Routing Worker for Recoup B2B Receivables Agent.
 *
 * Intercepts customer email replies routed by Cloudflare Email Routing,
 * parses raw MIME messages with PostalMime, computes Svix-compatible HMAC signatures,
 * and delivers the structured payload to the Recoup backend at /api/v1/replies.
 */

import PostalMime from "postal-mime";

export interface Env {
  BACKEND_URL: string;
  WEBHOOK_SECRET: string;
  LOG_PAYLOAD?: string;
}

/**
 * Decodes the webhook secret into bytes for WebCrypto HMAC.
 * Replicates the Python backend's Svix decoding:
 * Strips "whsec_", then attempts base64 decode, falling back to UTF-8.
 */
function decodeSecretKey(secret: string): Uint8Array {
  const raw = secret.startsWith("whsec_") ? secret.slice("whsec_".length) : secret;
  try {
    const binary = atob(raw);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    // Verify valid base64 round-trip
    if (btoa(binary) === raw) {
      return bytes;
    }
  } catch {
    // Not valid base64, fall back to UTF-8
  }
  return new TextEncoder().encode(raw);
}

/**
 * Computes Svix-compatible HMAC-SHA256 signature:
 * signature = Base64(HMAC_SHA256(key, `${messageId}.${timestamp}.${body}`))
 */
async function computeSvixSignature(
  body: string,
  secret: string,
  messageId: string,
  timestamp: string
): Promise<string> {
  const keyBytes = decodeSecretKey(secret);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const dataToSign = new TextEncoder().encode(`${messageId}.${timestamp}.${body}`);
  const signatureBuffer = await crypto.subtle.sign("HMAC", cryptoKey, dataToSign);
  const signatureBytes = new Uint8Array(signatureBuffer);
  let binary = "";
  for (let i = 0; i < signatureBytes.byteLength; i++) {
    binary += String.fromCharCode(signatureBytes[i]);
  }
  return btoa(binary);
}

/**
 * Forward parsed email payload to the Recoup backend with authentication headers.
 */
async function forwardToBackend(
  payload: Record<string, unknown>,
  env: Env
): Promise<{ status: number; text: string }> {
  const backendBase = (env.BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "");
  const targetUrl = `${backendBase}/api/v1/replies`;

  const body = JSON.stringify(payload);
  const messageId = String(payload.message_id || `cf-${Date.now()}`);
  const timestamp = Math.floor(Date.now() / 1000).toString();

  let svixSignature = "";
  if (env.WEBHOOK_SECRET) {
    try {
      const sig = await computeSvixSignature(body, env.WEBHOOK_SECRET, messageId, timestamp);
      svixSignature = `v1,${sig}`;
    } catch (err) {
      console.error("[Recoup Worker] Error computing Svix signature:", err);
    }
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "svix-id": messageId,
    "svix-timestamp": timestamp,
    "svix-signature": svixSignature,
    "x-worker-secret": env.WEBHOOK_SECRET || "",
    "User-Agent": "Recoup-Cloudflare-Email-Worker/1.0",
  };

  if (env.LOG_PAYLOAD === "true") {
    console.log("[Recoup Worker] Forwarding payload to:", targetUrl);
    console.log("[Recoup Worker] Payload preview:", JSON.stringify(payload).slice(0, 500));
  }

  const response = await fetch(targetUrl, {
    method: "POST",
    headers,
    body,
  });

  const responseText = await response.text();
  console.log(
    `[Recoup Worker] Backend response ${response.status} for message ${messageId}:`,
    responseText.slice(0, 300)
  );

  return { status: response.status, text: responseText };
}

export default {
  /**
   * Cloudflare Email Routing handler.
   * Triggered whenever an email matches a routing rule pointing to this worker.
   */
  async email(message: ForwardableEmailMessage, env: Env, ctx: ExecutionContext): Promise<void> {
    console.log(
      `[Recoup Worker] Inbound email from "${message.from}" to "${message.to}" (size: ${message.rawSize} bytes)`
    );

    try {
      // 1. Read raw MIME stream into an ArrayBuffer
      const rawBytes = await new Response(message.raw).arrayBuffer();

      // 2. Parse MIME headers and body parts using PostalMime
      const parser = new PostalMime();
      const parsed = await parser.parse(rawBytes);

      // 3. Collect all recipient addresses (To, CC, envelope To)
      const toAddresses = new Set<string>();
      if (message.to) toAddresses.add(message.to);
      if (Array.isArray(parsed.to)) {
        for (const recipient of parsed.to) {
          if (recipient.address) toAddresses.add(recipient.address);
        }
      }

      const ccAddresses = new Set<string>();
      if (Array.isArray(parsed.cc)) {
        for (const recipient of parsed.cc) {
          if (recipient.address) ccAddresses.add(recipient.address);
        }
      }

      // 4. Normalize message ID
      const messageId =
        parsed.messageId ||
        message.headers.get("message-id") ||
        `cf-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;

      // 5. Build structured payload matching Recoup's ingest schema
      const payload: Record<string, unknown> = {
        message_id: messageId,
        from: parsed.from?.address || message.from,
        from_name: parsed.from?.name || "",
        to: Array.from(toAddresses),
        cc: Array.from(ccAddresses),
        subject: parsed.subject || message.headers.get("subject") || "",
        text: parsed.text || "",
        html: parsed.html || "",
        date: parsed.date || new Date().toISOString(),
      };

      // 6. Forward to backend
      const result = await forwardToBackend(payload, env);
      if (result.status >= 400) {
        console.error(
          `[Recoup Worker] Backend rejected inbound reply (${result.status}): ${result.text}`
        );
      }
    } catch (error) {
      console.error("[Recoup Worker] Failed processing inbound email:", error);
      // We don't call message.setReject() here to prevent sender bounce loops,
      // but the error is logged in Cloudflare observability / wrangler tail.
    }
  },

  /**
   * HTTP Handler for health check and direct test simulations.
   */
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Health check endpoint
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return new Response(
        JSON.stringify({
          service: "recoup-email-worker",
          status: "healthy",
          configured_backend: env.BACKEND_URL ? "configured" : "missing",
          webhook_secret_set: Boolean(env.WEBHOOK_SECRET),
          time: new Date().toISOString(),
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // Direct test simulation endpoint: lets you simulate a reply without email propagation
    if (request.method === "POST" && url.pathname === "/test-simulate") {
      try {
        const body = await request.json<Record<string, unknown>>();
        const payload: Record<string, unknown> = {
          message_id: body.message_id || `sim-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`,
          from: body.from || "customer@example.com",
          to: Array.isArray(body.to) ? body.to : [String(body.to || "reply+INV-TEST@example.com")],
          cc: Array.isArray(body.cc) ? body.cc : [],
          subject: body.subject || "Re: Invoice Payment",
          text: body.text || "We will pay this by Friday",
          html: body.html || `<p>${body.text || "We will pay this by Friday"}</p>`,
          date: new Date().toISOString(),
        };

        const result = await forwardToBackend(payload, env);
        return new Response(result.text, {
          status: result.status,
          headers: { "Content-Type": "application/json" },
        });
      } catch (err: any) {
        return new Response(JSON.stringify({ error: err?.message || String(err) }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    return new Response(JSON.stringify({ error: "Not Found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  },
};
