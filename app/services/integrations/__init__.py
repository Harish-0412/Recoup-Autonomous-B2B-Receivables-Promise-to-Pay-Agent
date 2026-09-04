"""ERP integration services package.

Each sub-module implements one provider. They all share the IngestResult
dataclass and the same repository-layer calls (upsert_customer, upsert_invoice)
so adding a new provider is a matter of writing an adapter, not changing the
ingest logic.

Provider   Status
--------   ------
zoho       OAuth2, live Zoho Books API
quickbooks Intuit OAuth2, live QBO V3 API
razorpay   Razorpay Invoices API (same keys as payment links)
tally      File upload (CSV or XML); no cloud REST API
"""
