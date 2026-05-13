# Low-cost AWS EC2 Deployment

This deployment runs the Docker Compose edge hub on one small Ubuntu EC2 instance. It is intended for a demo or capstone environment, not a hardened production deployment.

## Instance Setup

1. Launch an Ubuntu EC2 instance with inbound TCP `22` for SSH and TCP `8080` for the web demo.
2. Use a low-cost Ubuntu image and a small instance type that can run Docker.
3. SSH into the instance and install Docker plus the Compose plugin:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"
```

4. Sign out and back in so the Docker group change applies.
5. Create the application directory:

```bash
sudo mkdir -p /opt/peacepulse/app
sudo chown -R "$USER:$USER" /opt/peacepulse
```

## GitHub Actions Secrets

Add these repository secrets before running the `Deploy EC2` workflow:

- `EC2_HOST`: public DNS name or public IP address for the instance.
- `EC2_USER`: SSH user, usually `ubuntu` for Ubuntu AMIs.
- `EC2_SSH_KEY`: private SSH key with access to the instance.

Before the first production deployment, set `PEACEPULSE_JWT_SECRET` and `PEACEPULSE_BOOTSTRAP_TOKEN` in the instance environment or Compose environment to long random values. The production API refuses to start with the default JWT secret or without a bootstrap token. The deploy workflow copies the repository to `/opt/peacepulse/app` with `rsync`, then runs:

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

## Verification

After deployment, verify the service from your machine:

```bash
curl http://EC2_HOST:8080/api/health
```

On the EC2 instance, verify the running container and logs:

```bash
cd /opt/peacepulse/app
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs --tail=80 api
```

The production health endpoint is `/api/v1/health` and should include `"database": "ok"`. If the browser cannot reach the app, check the instance security group, local firewall rules, and whether Docker mapped `8080:8080`.

## Backups

The low-cost production profile uses SQLite on the EC2 volume. Schedule `infra/backup-sqlite.sh` from cron or a systemd timer and copy the generated files under `data/backups/` to durable storage. The script checkpoints WAL, creates a consistent SQLite backup, runs `PRAGMA integrity_check`, and removes backups older than 14 days.

Example host cron entry:

```cron
15 * * * * cd /opt/peacepulse/app && docker compose -f infra/docker-compose.yml exec -T api /app/infra/backup-sqlite.sh
```

## Troubleshooting

- If the workflow skips deployment, confirm `EC2_HOST` is set because the job is guarded by that secret.
- If SSH fails, confirm the `EC2_USER` matches the AMI and the private key has not been pasted with extra spaces.
- If `curl` health checks fail but Docker is running, inspect `docker compose` logs for port binding or database path errors.
- If uploads fail in the browser, confirm the file is an image, audio file, text file, or PDF under 2 MB.

## Runtime Data

The Compose stack stores SQLite data under `/opt/peacepulse/app/data` through `PEACEPULSE_DB_PATH=/app/data/peacepulse.db`. Evidence files are stored under the same data tree and are not committed to git.

For a clean demo reset, stop the container and remove only the runtime data directory:

```bash
cd /opt/peacepulse/app
docker compose -f infra/docker-compose.yml down
rm -rf data
docker compose -f infra/docker-compose.yml up -d --build
```

Do not run that reset command on an instance that contains real submissions.
