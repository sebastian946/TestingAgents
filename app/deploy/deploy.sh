#!/usr/bin/env bash
# Deploy one commit on the production server and roll back if it is not healthy (WTA-38).
#
#   app/deploy/deploy.sh <commit-sha>
#
# Normally run by GitHub Actions through ssh-entry.sh (the forced command of the deploy key),
# but it works by hand too, as root in /opt/testing-agents. Steps:
#   1. lock (one deploy at a time) and remember the running commit;
#   2. dump the database (a migration runs on start and cannot be undone automatically);
#   3. check out the commit and `docker compose up -d --build`;
#   4. smoke test (waits for /Health/ready); on failure, check out the previous commit,
#      rebuild, smoke test again and exit 1 so the workflow shows red.
# The whole body is a function: git rewrites this file during the deploy, and bash must have
# parsed it all before that happens.

main() {
  set -euo pipefail

  local sha="${1:-}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: deploy.sh <40-character commit sha>" >&2; exit 2; }

  # ssh-entry.sh runs a temporary copy of this file, so the repository comes from REPO_DIR
  local repo="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  cd "$repo"
  local backups="${BACKUP_DIR:-/var/backups/testing-agents}"
  local dc=(docker compose --env-file .env -f app/docker-compose.yml -f app/docker-compose.prod.yml)

  log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

  exec 9>/tmp/tests-wise-deploy.lock
  flock -n 9 || { log "another deploy is running"; exit 3; }

  local prev
  prev="$(git rev-parse HEAD)"
  log "deploying ${sha:0:7} (running ${prev:0:7})"

  git fetch --quiet origin
  git cat-file -e "${sha}^{commit}" 2>/dev/null || { log "commit $sha not found on origin"; exit 2; }

  # 2. Database dump before any migration can run
  mkdir -p "$backups"
  local dump
  dump="$backups/predeploy-$(date -u +%F-%H%M)-${prev:0:7}.sql.gz"
  # The credentials come from the container's own environment: .env is not valid bash
  # (EMAIL_FROM=Name <address> is fine for docker compose, a syntax error for `source`)
  if docker exec postgres_db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$dump"; then
    log "database dumped to $dump ($(du -h "$dump" | cut -f1))"
  else
    log "database dump failed: refusing to deploy"; rm -f "$dump"; exit 4
  fi
  ls -1t "$backups"/predeploy-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f  # keep 10

  # 3. The new commit (a failed build counts as a failed deploy: it rolls back too)
  git reset --quiet --hard "$sha"
  log "building and starting containers"
  local started=0
  "${dc[@]}" up -d --build --remove-orphans && started=1

  # 4. Health, or roll back
  if (( started )) && bash app/deploy/smoke.sh; then
    docker image prune -f >/dev/null 2>&1 || true
    log "deployed ${sha:0:7}"
    return 0
  fi

  set +e
  log "deploy of ${sha:0:7} failed (build or smoke test): rolling back to ${prev:0:7}"
  "${dc[@]}" logs --tail 80 api worker || true
  git reset --quiet --hard "$prev"
  "${dc[@]}" up -d --build --remove-orphans
  if bash app/deploy/smoke.sh; then
    log "rolled back to ${prev:0:7}; the failed commit is ${sha:0:7}"
  else
    log "ROLLBACK ALSO UNHEALTHY. If a migration ran, restore: gunzip -c $dump | docker exec -i postgres_db sh -c 'psql -U \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"'"
  fi
  exit 1
}

main "$@"
