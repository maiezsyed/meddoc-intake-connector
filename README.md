# meddoc-intake-connector
**Athena Med Doc Intake Connector**
**Overview**

Meddoc Intake Connector is a lightweight backend service that helps clinics centralize patient intake documents and make them searchable. Phase‑1 integrates with the Athenahealth Preview (sandbox) environment to associate documents with sandbox patient identifiers while keeping files in your clinic’s Azure storage. It’s designed for backend, server‑to‑server use and does not require end‑user login.

Upload: Accepts files from automated workflows (e.g., Azure Logic Apps, scanners, email parsers)

Index: Stores metadata in Cosmos DB and text/snippets in Azure AI Search

Retrieve: Returns time‑limited SAS links for secure viewing/download

Environment: Athenahealth Preview (sandbox). No production EHR access in Phase‑1.

**What this is not**

Not an ONC‑Certified API app (CAPI)

Not a patient‑facing Personal Health Record (PHR) app

Not a medical device and does not provide clinical decision support

**Key Capabilities**

Secure ingestion via HTTPS API (OAuth2 client‑credentials)

Fast search across patient docs using Azure AI Search

Short‑lived access URLs (SAS) for downloads

Audit‑friendly metadata: patientId, docType, tags, timestamps

**Architecture (Phase‑1)**

[Logic App / Automations] → POST /documents → [Blob Storage + Cosmos DB] → Index in Azure AI Search
                                                        ↓
                                       GET /search, GET /documents/{id} (SAS links)

Files: Azure Blob Storage (private container)

Metadata: Cosmos DB (/patientId partition)

Search: Azure AI Search (text/snippets)

API: Azure Functions (protected by Entra ID OAuth2)

**Security & Privacy**

AuthN/AuthZ: OAuth2 (client‑credentials). Backend‑only; no end‑user login.

Data at rest: Encrypted by Azure Storage/Cosmos defaults.

Data in transit: HTTPS/TLS 1.2+

Access links: SAS tokens with short expiry (e.g., ≤10 minutes)

PII/PHI handling: This preview is intended for sandbox test data only. Do not upload real PHI without appropriate agreements (e.g., BAA) and production hardening.

For production use, we’ll add BAAs, secret management (Key Vault/Managed Identity), DLP/redaction, and EHR write‑back workflows.

**App Classification**

Type: System‑to‑system backend service

OAuth flow: 2‑legged OAuth (client credentials)

ONC CAPI: No (not certified); uses standard sandbox APIs

**Getting Started (Sandbox)**

Request access to a demo or share your sandbox patient identifiers.

We’ll provide an API base URL and scopes for token retrieval.

Use our Postman collection (or curl) to:

POST /documents with a file or SAS URL

GET /search?patientId=...&q=...

GET /documents/{id} to retrieve a SAS link

Typical sandbox practice ID: 195900 (example only; app configuration may vary).

**Support & Contact**

All inquiries: **maiez.syed@gmail.com**

**Legal
**
© Maiez Syed, 2025. All rights reserved.

This preview is provided “as is” without warranties and is intended for testing in non‑production environments.

Trademarks and product names are the property of their respective owners.
