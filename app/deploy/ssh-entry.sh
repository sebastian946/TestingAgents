#!/usr/bin/env bash
# Forced command of the GitHub Actions deploy key (WTA-38). In /root/.ssh/authorized_keys:
#
#   restrict,command="/opt/testing-agents/app/deploy/ssh-entry.sh" ssh-ed25519 AAAA... github-deploy
#
# Whatever the client asks for arrives in $SSH_ORIGINAL_COMMAND; only two requests are
# accepted, so a leaked key can deploy a commit of this repository or run the read-only
# smoke test, and nothing else (no shell, no port forwarding: `restrict`).
#
#   deploy <40-char sha>   fetch, check out that commit, rebuild, smoke test, roll back on failure
#   smoke                  the smoke test only

main() {
  set -euo pipefail
  local repo
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  local request="${SSH_ORIGINAL_COMMAND:-}"

  case "$request" in
    "deploy "*)
      local sha="${request#deploy }"
      [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "refused: not a commit sha" >&2; exit 2; }
      cd "$repo"
      git fetch --quiet origin
      # Run the deploy script of the commit being deployed, not the copy on disk: a change
      # to deploy.sh takes effect in the same deploy
      local script
      script="$(mktemp)"
      git show "${sha}:app/deploy/deploy.sh" > "$script"
      REPO_DIR="$repo" exec bash "$script" "$sha"
      ;;
    "smoke")
      exec bash "$repo/app/deploy/smoke.sh"
      ;;
    *)
      echo "refused: only 'deploy <sha>' and 'smoke' are allowed" >&2
      exit 2
      ;;
  esac
}

main "$@"
