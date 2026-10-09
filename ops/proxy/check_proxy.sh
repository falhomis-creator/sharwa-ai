#!/usr/bin/env bash
# ops/proxy/check_proxy.sh - P4 Task 17: verify the public front door of sharwa_ai.
#
# Run AFTER the proxy is up (Caddy or nginx). Read-only: only GET/HEAD requests
# plus one oversized POST that the proxy itself must refuse (413) before it
# reaches the api. Never sends a token, a webhook signature or a message.
#
#   bash ops/proxy/check_proxy.sh                       # https://ai.sharwaah.com
#   bash ops/proxy/check_proxy.sh https://api.example   # another host
#   CURL_OPTS="-k --resolve ai.sharwaah.com:443:127.0.0.1 --resolve ai.sharwaah.com:80:127.0.0.1" \
#       bash ops/proxy/check_proxy.sh                   # local test (self-signed cert)
#
# Exit 0 = every check passed; 1 = at least one failed (each failure is printed).
set -u
BASE="${1:-https://ai.sharwaah.com}"
HOST="${BASE#https://}"; HOST="${HOST%%/*}"
read -r -a OPTS <<< "${CURL_OPTS:-}"
PASS=0; FAIL=0

ok()  { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s (got %s)\n' "$1" "$2"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${OPTS[@]}" "$@"; }

# 1. paths that must reach the api (anything but a proxy 404 / 5xx)
for p in /healthz /console/ /v1/me /webhooks/platform/catalog /webhooks/platform/cart /webhooks/platform/gift-cart; do
    c=$(code "$BASE$p")
    case "$c" in 404|000|5??) bad "reaches api: GET $p" "$c" ;; *) ok "reaches api: GET $p -> $c" ;; esac
done

# 2. paths that must NEVER be public (the proxy answers 404 itself)
for p in / /metrics /readyz /docs /redoc /openapi.json /console-x /v1 /webhooks/platform/other /.env; do
    c=$(code "$BASE$p")
    [ "$c" = "404" ] && ok "blocked: GET $p -> 404" || bad "blocked: GET $p must be 404" "$c"
done

# 3. /console without the slash -> permanent redirect to /console/
loc=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "${OPTS[@]}" "$BASE/console")
case "$loc" in 30[18]\ *"/console/") ok "redirect: /console -> $loc" ;; *) bad "redirect: /console -> /console/" "$loc" ;; esac

# 4. plain http -> https
loc=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "${OPTS[@]}" "http://$HOST/healthz")
case "$loc" in 30[1278]\ https://*) ok "http -> https: $loc" ;; *) bad "http must redirect to https" "$loc" ;; esac

# 5. security headers: HSTS present, no Server banner
hdrs=$(curl -s -D - -o /dev/null --max-time 15 "${OPTS[@]}" "$BASE/healthz" | tr -d '\r')
if [ -z "$hdrs" ]; then
    bad "security headers" "no response"
else
    grep -qi '^strict-transport-security: max-age=31536000' <<< "$hdrs" && ok "HSTS header" || bad "HSTS header" "missing"
    # Caddy sends no Server header; nginx sends a bare "Server: nginx". A version number fails.
    grep -qiE '^server:.*[0-9]' <<< "$hdrs" && bad "no server version leaked" "$(grep -i '^server:' <<< "$hdrs")" || ok "no server version leaked"
fi

# 6. an oversized webhook body (600 KB) is refused by the proxy (413)
c=$(head -c 614400 /dev/zero | tr '\0' 'a' | code -X POST -H 'Content-Type: application/json' --data-binary @- "$BASE/webhooks/platform/catalog")
[ "$c" = "413" ] && ok "webhook body > 512 KB -> 413" || bad "webhook body > 512 KB must be 413" "$c"

# 7. TLS: the certificate is valid for the host (skipped when CURL_OPTS has -k)
if [[ " ${OPTS[*]-} " == *" -k "* ]]; then
    printf 'SKIP  certificate check (-k in CURL_OPTS)\n'
else
    c=$(code "$BASE/healthz")
    [ "$c" != "000" ] && ok "certificate valid for $HOST" || bad "certificate valid for $HOST" "$c"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
