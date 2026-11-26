# ALTRUST FLEX STOCKS — Kenyan Farm Marketplace
FastAPI + SQLAlchemy (PostgreSQL in production, SQLite local/offline mode), bcrypt, JWT, role-based access.

## Run
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env   # then export its values (or set them in your shell)
    uvicorn app.main:app --reload
Open http://localhost:8000 to browse products. Admin dashboard: /admin. API docs: /docs.
For PostgreSQL: `docker compose up -d`, then set DATABASE_URL (see .env.example).

## Included
- Auth: bcrypt hashing, JWT (8h), customer/provider registration, 7 admin roles (super, verification, operations, finance, support, catalogue, analytics)
- Admin dashboard: overview, users, sellers, seller payout numbers, products, product orders, store activity, providers, verification docs, services, manual escrow, payments, reviews, disputes, audit log, settings
- Global catalogue: 26 industries with subcategories seeded; unlimited depth, add/disable anything from the UI (no code change)
- Provider verification: document upload to private storage, status workflow, every document view is audit-logged
- Audit trail on every admin write
- Public product catalogue, seller listings with photos and seller-set prices, stock quantities and counties
- Customer accounts, delivery address/location capture, orders and order-status tracking
- Seller M-Pesa receiving numbers, seller order view and an admin activity feed
- M-Pesa Daraja STK Push and Stripe Checkout integration points with signed/secret callback handling

## Not yet built (next steps)
Automated payouts/split settlement to sellers, notification sending (SMS/email/push), offline sync engine
(SQLite local mode + `synced` flag on bookings are the groundwork), analytics charts, customer/provider web portals,
file encryption at rest, refresh tokens, migrations (Alembic), rate limiting. Set a strong SECRET_KEY and change the seeded admin password before any real use.

## v2: portals + manual escrow
- Customer & provider portal: `/`  |  Staff admin dashboard: `/admin`
- Providers register, fill profile (services, payout details), upload ID/licences (private storage, JPG/PNG/PDF, 8 MB). Admin cannot approve a provider without an ID document and payout details.
- Manual escrow flow: customer books -> pays platform by M-Pesa/bank (instructions editable in Admin > Settings: `escrow_instructions`) and enters the code ->
  Finance admin checks the platform account and clicks **confirm** -> provider delivers -> customer approves (or disputes) ->
  Finance admin pays the provider by hand and records the payout reference (**release**), or pays the customer back (**refund**). Every step is logged (Escrow > history).
- Payment codes can't be reused across bookings. If you used v1 before, delete `fambeermconn_local.db` once (new tables/columns).

## Going online free: Render (web) + Neon (database)
Uploaded IDs are now stored in the database, so no paid disk is needed.
1. neon.tech: create a free project, copy the connection string (starts with postgresql://).
2. render.com: New > Blueprint > pick your GitHub repo (render.yaml is read automatically).
3. Enter DATABASE_URL (the Neon string), ADMIN_EMAIL and ADMIN_PASSWORD. SECRET_KEY is generated.
4. When live, "/" = public ALTRUST FLEX STOCKS product marketplace, "/admin" = staff. Free web services sleep after ~15 min idle: open the link a minute before a presentation.
Free-tier terms change; check each provider's current limits. If you used an older version locally, delete fambeermconn_local.db once.

## Other hosts (no render.yaml needed)
A Dockerfile and Procfile are included, so Railway, Hugging Face Spaces (Docker), SnapDeploy, Koyeb etc. can build it directly from GitHub.
Set these variables on the host: DATABASE_URL (e.g. a free Neon PostgreSQL string), SECRET_KEY (long random text), ADMIN_EMAIL, ADMIN_PASSWORD.


## Legacy service and escrow APIs
- Existing service, provider verification and manual escrow APIs remain available for existing records and integrations.
- Manual escrow remains a separate flow from the ALTRUST FLEX STOCKS product order and checkout flow below.

## ALTRUST FLEX STOCKS product marketplace and payments
- The home page is a public product catalogue. Customers can browse before signing in; checkout requires a customer account and a delivery county/address. GPS coordinates can be shared from the browser if the customer allows location access.
- Sellers create a seller account with their Kenyan M-Pesa receiving number, then publish products with their own KES price, unit, stock, county, description and optional JPG/PNG/WebP photo (up to 5 MB). Images are stored under `private_uploads/products`.
- Orders are paid to the marketplace's M-Pesa shortcode or Stripe account. The registered seller M-Pesa number is recorded for the marketplace operator to settle seller proceeds. This build does not automatically split or pay out funds to sellers; configure and operate seller payouts before accepting live orders.
- M-Pesa uses Daraja STK Push. Set `MPESA_ENV=sandbox` while testing and provide `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, `MPESA_PASSKEY`, and a random `MPESA_CALLBACK_TOKEN`. The callback must be reachable over HTTPS at the exact `MPESA_CALLBACK_URL`; configure that same URL with Safaricom. Change to `MPESA_ENV=production` only after Safaricom has enabled your production app and shortcode.
- Card checkout uses Stripe Checkout. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, and register the webhook URL `/api/payments/stripe/webhook` for `checkout.session.completed` and `checkout.session.expired`. Card details are entered on Stripe, not stored by this application. Confirm that your Stripe account can process KES.
- Never put production keys in source control or send them in chat. Put them in the hosting provider's secret environment settings. A local `127.0.0.1` server is not publicly reachable by payment callbacks; deploy behind HTTPS with a public callback URL before enabling live checkout.
- `/admin` includes Product orders, Seller payout numbers, Products, and Store activity. Activity records account, listing, order, checkout and payment state changes; limit access to trusted staff. Delivery/settlement still needs staff operations and is not automated.
- Default admin: admin@fambeermconn.local / ChangeMe!2026 (change it with setpw.py).
