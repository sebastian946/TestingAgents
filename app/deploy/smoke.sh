#!/usr/bin/env bash
# Smoke test of a running Tests Wise deployment (WTA-38). Read-only: it never creates an
# account, a job or a payment, so it is safe against production at any time.
#
#   app/deploy/smoke.sh                          # on the server: https://$DOMAIN from .env
#   app/deploy/smoke.sh https://app.example.com  # anywhere (CI, your laptop)
#
# It first waits up to READY_TIMEOUT seconds for GET /Health/ready (database migrated, Redis,
# a worker listening), then checks what a visitor and the payment provider can reach. Exit 0
# when everything passes, 1 otherwise; the last lines say which check failed.

main() {
  set -uo pipefail

  local repo base
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  base="${1:-}"
  if [[ -z "$base" ]]; then
    local domain
    domain="$(grep -E '^DOMAIN=' "$repo/.env" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"' \r')"
    [[ -n "$domain" ]] || { echo "usage: smoke.sh https://host (or set DOMAIN in .env)" >&2; exit 2; }
    base="https://$domain"
  fi
  base="${base%/}"
  local ready_timeout="${READY_TIMEOUT:-240}"
  local failures=0 body status

  pass() { printf '  ok    %s\n' "$1"; }
  fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }
  # fetch URL [curl args...] -> sets $status and $body
  fetch() {
    local url="$1"; shift
    body="$(curl -sS --max-time 15 -o - -w '\n%{http_code}' "$@" "$url" 2>/dev/null)" || body=$'\n000'
    status="${body##*$'\n'}"
    body="${body%$'\n'*}"
  }
  expect() {  # expect NAME PATH CODE [TEXT] [curl args...]
    local name="$1" path="$2" code="$3" text="${4:-}"
    shift 4 2>/dev/null || shift $#
    fetch "$base$path" "$@"
    if [[ "$status" != "$code" ]]; then
      fail "$name: $path answered $status, expected $code"
    elif [[ -n "$text" && "$body" != *"$text"* ]]; then
      fail "$name: $path does not contain \"$text\""
    else
      pass "$name"
    fi
  }

  echo "Smoke test of $base"

  # 1. Readiness: poll, because right after a deploy the containers are still starting
  local deadline=$((SECONDS + ready_timeout))
  # A 200 is not enough: if Caddy does not route /Health/* to the API, the dashboard's
  # index.html answers 200 too. Only the API's JSON with "status":"ready" counts
  local is_ready=0
  while :; do
    fetch "$base/Health/ready"
    if [[ "$status" == "200" && "$body" == *'"status":"ready"'* ]]; then is_ready=1; break; fi
    if (( SECONDS >= deadline )); then
      fail "ready: /Health/ready answered $status after ${ready_timeout}s: ${body:0:160}"
      break
    fi
    sleep 5
  done
  (( is_ready )) && pass "ready: database, migrations, Redis and a worker ($body)"

  # 2. What a visitor sees
  expect "liveness" /Health 200 '"healthy"'
  expect "landing page" / 200 "Tests Wise"
  expect "landing styles" /landing.css 200 "--brand"
  expect "dashboard app shell" /login 200 'id="root"'
  expect "terms" /terms 200 "Terms"
  expect "privacy" /privacy 200 "Privacy"
  expect "robots.txt" /robots.txt 200 "Sitemap:"
  expect "sitemap" /sitemap.xml 200 "<urlset"
  status="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$base/og-image.png" 2>/dev/null || echo 000)"
  [[ "$status" == "200" ]] && pass "share image" || fail "share image: /og-image.png answered $status"

  # 3. The API behind Caddy: auth required, billing webhook reachable and signed
  expect "API requires login" /auth/me 401 ""
  fetch "$base/billing/webhook/lemonsqueezy" -X POST -H "Content-Type: application/json" -H "X-Signature: smoke" --data '{}'
  case "$status" in
    400) pass "billing webhook verifies signatures" ;;
    503) fail "billing webhook: payments are not configured on the server (LEMONSQUEEZY_WEBHOOK_SECRET)" ;;
    *) fail "billing webhook answered $status, expected 400 for a bad signature" ;;
  esac

  # 4. Security headers and the certificate
  local headers
  headers="$(curl -sSI --max-time 15 "$base/" 2>/dev/null | tr -d '\r')"
  [[ "$headers" == *"strict-transport-security"* || "$headers" == *"Strict-Transport-Security"* ]] \
    && pass "HSTS header" || fail "HSTS header missing"
  [[ "$headers" == *"content-security-policy"* || "$headers" == *"Content-Security-Policy"* ]] \
    && pass "CSP header" || fail "CSP header missing"
  if [[ "$base" == https://* ]] && command -v openssl >/dev/null; then
    local host="${base#https://}"; host="${host%%/*}"
    local end
    end="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
    if [[ -z "$end" ]]; then
      fail "TLS certificate could not be read"
    else
      local days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
      (( days >= 14 )) && pass "TLS certificate valid for $days more days" || fail "TLS certificate expires in $days days"
    fi
  fi

  echo
  if (( failures > 0 )); then
    echo "SMOKE FAILED: $failures check(s)"
    exit 1
  fi
  echo "SMOKE OK"
}

main "$@"
