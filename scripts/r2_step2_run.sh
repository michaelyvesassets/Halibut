#!/usr/bin/env bash
# R2 STEP 2 pre-registered benchmark of the loop.v2 contract (PREPARED; running it needs the founder's approval).
# Usage: bash scripts/r2_step2_run.sh 2a   (then, separately approved, 2b). Run from the repository, in a terminal where
# ANTHROPIC_API_KEY is set. Plan and criteria: docs/architecture/R2_STEP2_PREREGISTRATION.md (criteria hash pinned by a
# test). Each run is its own single-use approval with worst case <= USD 0.10 (ADR-019).
#
# Safeguards (as in Step 1, all automatic): free API key check before any approval; single-use self-approved paid_run
# (attested); the approved provider config only in a temporary copy outside the repository; approval revoked and the
# copy removed on ANY exit; pipefail; the spend guard caps every attempt (smoke and retries included) at the cap.
#
# Environment (optional): HALIBUT_DB (default data/halibut.sqlite), DRY_RUN=1 (plan only; no paid call).
set -Eeuo pipefail

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
RUN="${1:?usage: r2_step2_run.sh 2a|2b}"
case "$RUN" in 2a|2b) ;; *) echo "unknown run $RUN (2a or 2b)"; exit 2 ;; esac
DB="${HALIBUT_DB:-data/halibut.sqlite}"
DRY_RUN="${DRY_RUN:-0}"
CAP="0.10"  # worst case: 2a $0.0946 (19 calls), 2b $0.0693 (15 calls); expected about $0.04 and $0.03
SUBJECT="r2_step2:${RUN}"
OPERATOR="founder"
CONFIG="configs/llm/anthropic.toml"
ATTEST="I, the founder and sole operator, authorise R2 Step 2 run ${RUN}: the pre-registered loop.v2 benchmark (fresh Claude Haiku calls on held-out and prospective origins, matched arms on identical evidence), hard cap USD ${CAP}, single use."
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="results/r2_step2/run${RUN}-${STAMP}"
mkdir -p "$RUN_DIR"
TMP_DIR="$(mktemp -d)"
chmod 700 "$TMP_DIR"
APPROVAL=""
CONFIG_SHA_BEFORE="$(sha256sum "$CONFIG" | cut -d' ' -f1)"

log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$RUN_DIR/execution.log"; }

cleanup() {
  local rc=$?
  set +e
  trap - EXIT INT TERM
  if [ -n "$APPROVAL" ]; then
    if halibut approvals --db "$DB" decide "$APPROVAL" revoked --by "$OPERATOR" \
         --note "automatic revocation at the end of the run (exit code $rc)" >>"$RUN_DIR/execution.log" 2>&1; then
      log "approval $APPROVAL revoked"
    else
      log "ERROR: could not revoke $APPROVAL; revoke it manually: halibut approvals decide $APPROVAL revoked --by $OPERATOR"
    fi
    halibut approvals --db "$DB" show "$APPROVAL" >"$RUN_DIR/approval_final.json" 2>>"$RUN_DIR/execution.log"
  fi
  rm -rf "$TMP_DIR" && log "temporary provider config removed"
  if [ "$(sha256sum "$CONFIG" | cut -d' ' -f1)" = "$CONFIG_SHA_BEFORE" ]; then
    log "tracked $CONFIG unchanged"
  else
    log "WARNING: $CONFIG changed during the run (not by this script)"
  fi
  halibut agents --db "$DB" spend >"$RUN_DIR/spend_after.json" 2>>"$RUN_DIR/execution.log"
  log "finished with exit code $rc; records in $RUN_DIR"
  exit "$rc"
}
trap cleanup EXIT
trap 'log "interrupted (SIGINT)"; exit 130' INT
trap 'log "terminated (SIGTERM)"; exit 143' TERM

log "R2 Step 2 run ${RUN} $( [ "$DRY_RUN" = 1 ] && echo '(DRY RUN: no paid call)' ) db=$DB"
if [ "$DRY_RUN" != 1 ]; then
  : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY must be set in this terminal}"
  # key check BEFORE any approval or spend reservation (r2r-dc620061d98c failed its smoke test with HTTP 401):
  # format check offline, then GET /v1/models, which is free (no tokens) and needs a valid key. The key is never printed.
  # Outcomes are classified; only fixed messages are printed (never the key, never exception text). Exit codes:
  # 10 format, 11 authentication (401), 12 permission (403), 13 rate limit (429), 14 provider outage (5xx/529),
  # 15 network or timeout, 16 other.
  set +e
  python - <<'PY' 2>>"$RUN_DIR/execution.log"
import os, re, sys
k = os.environ.get("ANTHROPIC_API_KEY", "")
def out(code, msg):
    sys.stderr.write(f"key check: {msg}\n")
    sys.exit(code)
if k.strip() != k or not re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{20,}", k):
    out(10, "FORMAT: ANTHROPIC_API_KEY is not a plausible Anthropic API key (placeholder, whitespace or wrong format)")
try:
    import anthropic
    anthropic.Anthropic(api_key=k, max_retries=0, timeout=20).models.list(limit=1)
except anthropic.AuthenticationError:
    out(11, "AUTHENTICATION (HTTP 401): the key was rejected; use a valid, active key")
except anthropic.PermissionDeniedError:
    out(12, "PERMISSION (HTTP 403): the key is valid but not permitted for this request or organisation")
except anthropic.RateLimitError:
    out(13, "RATE LIMIT (HTTP 429): retry later; the key itself was not rejected")
except anthropic.InternalServerError:
    out(14, "PROVIDER OUTAGE (HTTP 5xx/529): the API is unavailable or overloaded; retry later")
except (anthropic.APITimeoutError, anthropic.APIConnectionError):
    out(15, "NETWORK: could not reach the API (connection or timeout)")
except anthropic.APIStatusError as exc:
    out(14 if exc.status_code >= 500 else 16, f"UNEXPECTED HTTP {exc.status_code}")
except BaseException as exc:  # noqa: BLE001  never print exception text: it could echo request details
    out(16, f"UNEXPECTED {type(exc).__name__}")
PY
  KEYCHECK=$?
  set -e
  if [ "$KEYCHECK" != 0 ]; then
    log "refusing: API key check failed (code $KEYCHECK; see 'key check:' above) before any approval or paid call"
    exit 2
  fi
  log "API key check passed (GET /v1/models, no tokens, no charge)"
fi
if ! grep -q '^approved = false$' "$CONFIG"; then
  log "refusing: $CONFIG must keep approved = false (the second key is created only in a temporary copy)"
  exit 2
fi
halibut agents --db "$DB" spend >"$RUN_DIR/spend_before.json"

# 1. single-use approval record (sole-operator policy, ADR-019)
APPROVAL="$(halibut approvals --db "$DB" request paid_run "$SUBJECT" --by "$OPERATOR" --max-usd "$CAP" \
            --note "R2 Step 2 run ${RUN}: pre-registered loop.v2 benchmark (fresh calls; see docs/architecture/R2_STEP2_PREREGISTRATION.md)")"
log "approval requested: $APPROVAL"
halibut approvals --db "$DB" decide "$APPROVAL" granted --by "$OPERATOR" --attestation "$ATTEST" | tee -a "$RUN_DIR/execution.log"

# 2. temporary second key: an approved copy of the provider config outside the repository
TMP_CONFIG="$TMP_DIR/anthropic.approved.toml"
sed -e 's/^approved = false$/approved = true/' \
    -e "s/^approved_by = \"\"\$/approved_by = \"${OPERATOR}\"/" \
    -e "s/^approved_on = \"\"\$/approved_on = \"$(date -u +%F)\"/" "$CONFIG" >"$TMP_CONFIG"
chmod 600 "$TMP_CONFIG"
grep -q '^approved = true$' "$TMP_CONFIG"
log "temporary approved provider config: $TMP_CONFIG"

# 3. plan (refuses before ANY call if the worst case exceeds the cap) and the run
LIVE_FLAGS=(--live --approval "$APPROVAL" --provider-config "$TMP_CONFIG")
if [ "$DRY_RUN" = 1 ]; then
  LIVE_FLAGS=()
fi
halibut investigation --db "$DB" step2 "$RUN" --max-usd "$CAP" --out "$RUN_DIR" "${LIVE_FLAGS[@]}" 2>&1 | tee "$RUN_DIR/step2_output.log"
log "experiment command completed"
