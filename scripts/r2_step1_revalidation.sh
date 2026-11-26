#!/usr/bin/env bash
# R2 STEP 1 (mixed cached/live ENGINEERING revalidation of r2v-b16fc9281536), authorised by the founder on 2026-11-24
# (cap USD 0.04). The generator is NEVER called (recorded replies from cache); live: smoke + 2 critic + 2 strategist. Run from the repository, in a terminal where
# ANTHROPIC_API_KEY is set. Plan: docs/architecture/R2_STEP1_REVALIDATION_PLAN.md.
#
# Safeguards (all automatic):
#   - one single-use paid_run approval, self-approved under the sole-operator policy (ADR-019) with an attestation;
#   - the provider's second key (approved = true) exists only in a temporary copy outside the repository, deleted on
#     exit; the tracked configs/llm/anthropic.toml is never written (its checksum is verified at the end);
#   - on ANY exit (success, failure, error, Ctrl-C, kill) the approval is revoked and the temporary copy removed;
#   - pipefail: a failure of the experiment command is the script's failure, even through tee;
#   - the spend guard caps every attempt of the experiment (smoke and retries included) at USD 0.06.
#
# Environment (optional): HALIBUT_DB (default data/halibut.sqlite), R2_MARKET, DRY_RUN=1 (no paid call; for rehearsal).
set -Eeuo pipefail

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"
DB="${HALIBUT_DB:-data/halibut.sqlite}"
DRY_RUN="${DRY_RUN:-0}"
MARKET="${R2_MARKET:-configs/intel/markets/uk_cars_bev_v1.toml}"
ORIGINS=(2025-11-23 2026-05-23)
EVALUATE_AT="2026-11-23"
CAP="0.02"  # re-run after r2r-57fa83dc8f2f: smoke + 2026-05-23 critic + strategist; worst case $0.0087 ($0.0173 with retries)
SUBJECT="r2_revalidation:uk_cars_first_registrations"
ORIGINAL_RUN="${R2_ORIGINAL_RUN:-results/r2_validation/run-20261124T021311Z}"
# paid replies of these experiments are REUSED when the request is byte-identical (verified offline, then at call time)
REUSE_FROM="${R2_REUSE_FROM:-r2r-57fa83dc8f2f}"
LIVE_ORIGINS=(2026-05-23)  # the only origin allowed live critic/strategist calls
OPERATOR="founder"
CONFIG="configs/llm/anthropic.toml"
ATTEST="I, the founder and sole operator, authorise R2 Step 1: a mixed cached/live engineering revalidation of r2v-b16fc9281536 (generator replies from cache; 2025-11-23 critic and strategist reused from ${REUSE_FROM}; live smoke + the critic and strategist calls not already paid for), hard cap USD ${CAP}, single use."
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="results/r2_validation/step1-${STAMP}"
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

log "R2 Step 1 revalidation $( [ "$DRY_RUN" = 1 ] && echo '(DRY RUN: no paid call)' ) db=$DB"
if [ "$DRY_RUN" != 1 ]; then
  : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY must be set in this terminal}"
fi
if ! grep -q '^approved = false$' "$CONFIG"; then
  log "refusing: $CONFIG must keep approved = false (the second key is created only in a temporary copy)"
  exit 2
fi
halibut agents --db "$DB" spend >"$RUN_DIR/spend_before.json"

# 1. single-use approval record (sole-operator policy, ADR-019)
APPROVAL="$(halibut approvals --db "$DB" request paid_run "$SUBJECT" --by "$OPERATOR" --max-usd "$CAP" \
            --note "R2 Step 1 engineering revalidation: 5 live calls, generator from cache")"
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

# 3. identity preflight + revalidation (refuses before ANY call if the generator requests are not the recorded ones,
#    or if the plan exceeds the cap; the generator is cache-only)
[ -d "$ORIGINAL_RUN" ] || { log "refusing: original run directory $ORIGINAL_RUN not found"; exit 2; }
LIVE_FLAGS=(--live --approval "$APPROVAL" --provider-config "$TMP_CONFIG")
if [ "$DRY_RUN" = 1 ]; then
  LIVE_FLAGS=()
fi
halibut investigation --db "$DB" revalidate "$MARKET" --original-run "$ORIGINAL_RUN" --origins "${ORIGINS[@]}" \
  --evaluate-at "$EVALUATE_AT" --max-usd "$CAP" --out "$RUN_DIR" --reuse-from $REUSE_FROM --live-origins "${LIVE_ORIGINS[@]}" "${LIVE_FLAGS[@]}" 2>&1 | tee "$RUN_DIR/revalidate_output.log"
log "experiment command completed"
