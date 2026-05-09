# Low-cost AWS EC2 Deployment

This deployment runs the Docker Compose edge hub on one small Ubuntu EC2 instance.

## Instance Setup

1. Launch an Ubuntu EC2 instance.
2. Install Docker and the Compose plugin.
3. Create `/opt/peacepulse`.
4. Add GitHub Actions secrets:
   - `EC2_HOST`
   - `EC2_USER`
   - `EC2_SSH_KEY`
5. Trigger the `Deploy EC2` workflow or push to `main`.

## Runtime Data

The Compose stack stores SQLite data under `/opt/peacepulse/app/data` through the configured `PEACEPULSE_DB_PATH`.
