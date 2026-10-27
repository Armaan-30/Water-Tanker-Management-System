# Water Tanker Management System
A runnable implementation of the supplied municipality case study. Python 3.10+ and a modern browser are sufficient; no external packages or network installation required.

## Start
```sh
git clone https://github.com/Armaan-30/Water-Tanker-Management-System.git
cd Water-Tanker-Management-System
python app.py --seed
python app.py
```
Open http://localhost:8000. Demo users: `citizen@wtms.local`, `dispatcher@wtms.local`, `admin@wtms.local`. All use `DemoPass123!`. Seed only for local demonstration. Database is created automatically and persists across restarts.

## Workflows
Visitors search North, Central or South by date and capacity. Citizens register, log in, book available 09:00/13:00/17:00 UTC slots, view deliveries and cancel pending bookings at least 24 hours ahead. Residential and commercial limits default to 2 and 5 active bookings. Completed deliveries no longer count toward limits.

Staff use Operations to assign drivers and advance Pending → Dispatched → Delivered. Administrators add/edit drivers and tankers by ID, deactivate records (history is retained), change booking limits and inspect the audit log. A tanker has a default driver; dispatchers can override the delivery assignment. Fleet administrators must avoid assigning the same default driver to overlapping tanker schedules.

For account recovery, an operator verifies the citizen's identity, then runs:
```sh
python app.py --recover citizen@wtms.local
```
Deliver the resulting 15-minute, single-use token privately to the verified account owner. Enter it in Recover access. Only token hashes are stored. Automatic email sending is not configured.

## Architecture and security
HTML/CSS/JavaScript frontend → Python HTTP JSON API → SQLite. Passwords use salted scrypt. Sessions expire after 8 hours, use HttpOnly/SameSite cookies, and mutations require CSRF tokens. Three failed logins lock the account for 15 minutes. Parameterized queries, database uniqueness and serialized booking writes prevent duplicate accounts and slot reservations. Prices are captured at booking time. Staff mutation events are audited. No payment information is collected; prices are quotations in INR, an implementation assumption.

Staff endpoints require both a staff role and an allowed source network. Default is loopback only; configure `WTMS_STAFF_NETWORKS` with comma-separated municipal/VPN CIDRs. The application does not trust forwarded IP headers. Keep public and staff ingress isolated when using a proxy: a proxy's loopback address must not inadvertently authorize public requests to `/api/staff/*`.

## Verification
```sh
python -m unittest discover -s tests -v
node --check static/app.js  # optional, requires Node
```
Tests cover duplicate slots, account limits, cancellation timing, ownership, rebooking, fleet inactivity and password hashing.

## Deployment boundaries
This is a complete local project, not a production deployment. The built-in Python HTTP server and SQLite have not been certified for 500 concurrent users or 24/7 availability. For production use an appropriate production application server/database, load testing, backups, monitoring, request rate limits, HTTPS, verified email recovery and a municipal VPN/firewall. Set `WTMS_SECURE_COOKIE=1` behind HTTPS. Do not seed demo accounts in production. Change demo passwords via recovery before any shared use. All schedules use UTC; adapt the timezone and currency to the municipality before rollout.

Environment settings: `WTMS_DB` database path; `WTMS_STAFF_NETWORKS` trusted CIDRs; `WTMS_SECURE_COOKIE=1` HTTPS cookies. Port: `python app.py --port 8001`.

## Requirement mapping
R1: registration and email/mobile uniqueness. R2: login, lockout, recovery. R3: public availability and prices. R4: transactional reservation and configurable active limits. R5: cancellation with 24-hour cutoff and slot release. R6: delivery status and staff updates. Supporting administration includes driver/tanker editing, soft removal, dispatch assignment and audit records.
