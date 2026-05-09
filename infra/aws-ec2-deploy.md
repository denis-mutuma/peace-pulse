# Low-cost AWS EC2 deployment

This deployment keeps cost low by running the same Docker Compose stack on one small EC2 instance with an attached local volume.

## Instance setup

1. Launch an Ubuntu EC2 instance.
2. Install Docker and the Compose plugin.
3. Create `/opt/peacepulse`.
4. Clone the repository into `/opt/peacepulse/app`.
5. Run:

```bash
cd /opt/peacepulse/app
docker compose -f infra/docker-compose.yml up -d --build
```

## GitHub Actions variables

- `EC2_HOST`
- `EC2_USER`
- `EC2_SSH_KEY`

The workflow copies the repo, rebuilds containers, and restarts the edge hub.
