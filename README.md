# Fieldstatic landing page

Landing page for **fieldstatic.shop** with a working waitlist email collector
and a password-protected admin dashboard.

## Layout

- `site/` — the static landing page (`index.html` + `assets/`). The waitlist
  form POSTs to `/api/subscribe`.
- `server/app.py` — dependency-free Python (stdlib only) service: serves the
  static site, stores submitted emails in SQLite, and renders the admin
  dashboard. Endpoints:
  - `POST /api/subscribe` — `{ "email": "..." }`, stores email + UTC timestamp + IP + user agent.
  - `GET /admin` — HTTP Basic Auth dashboard (newest first, counts, CSV link).
  - `GET /admin/export.csv` — CSV export.
  - `GET /healthz` — health probe.
- `Dockerfile` — builds the service image (`python:3.12-alpine`).
- `deploy/fieldstatic-nginx-block.conf` — the nginx server blocks appended to
  the host's edge proxy config for `fieldstatic.shop`.

## Production deployment (server 5.42.110.221)

The host runs an unrelated Dockerized "courses" platform behind an nginx edge
proxy container (`courses_edge_proxy`, host networking, owns :80/:443,
config at `/root/courses-edge-nginx.conf`, Let's Encrypt at `/root/letsencrypt`).
This site was added alongside it **without modifying any courses service**:

1. Files live in `/root/fieldstatic/` on the server.
2. App runs as its own container, isolated SQLite at `/root/fieldstatic/data`:
   ```sh
   docker build -t fieldstatic-site:latest /root/fieldstatic
   docker run -d --name fieldstatic-site --restart unless-stopped \
     -p 127.0.0.1:18090:8080 \
     -v /root/fieldstatic/data:/data \
     -e ADMIN_USER=fieldstatic -e ADMIN_PASS='<secret>' \
     fieldstatic-site:latest
   ```
3. TLS cert issued via the existing certbot Docker method (webroot). Auto-renews
   via the existing daily `/root/renew-courses-certs.sh` cron (renews all certs).
4. The nginx block in `deploy/` was appended to `/root/courses-edge-nginx.conf`
   (a backup was taken first), validated with `nginx -t` inside the edge proxy,
   then `nginx -s reload`. It proxies `fieldstatic.shop` → `127.0.0.1:18090`.

### Updating the site later

```sh
# from this repo, copy changed files to the server, then:
docker build -t fieldstatic-site:latest /root/fieldstatic
docker rm -f fieldstatic-site && docker run -d ... (same as above)
```
The SQLite volume (`/root/fieldstatic/data`) persists across rebuilds, so
collected emails are never lost.

> The admin password is **not** stored in this repo (see `deploy/.admin_pass_local`,
> which is gitignored).
