#!/bin/bash
set -euo pipefail

HOST="${1:-10.5.38.87}"
USER_NAME="${2:-user}"
PASSWORD="${3:-111111}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"

SSH=(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
SCP=(sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
REMOTE="/home/${USER_NAME}/eol"

"${SSH[@]}" "${USER_NAME}@${HOST}" "mkdir -p '${REMOTE}/data/plans' '${REMOTE}/data/images' '${REMOTE}/_internal/scripts/arm'"

"${SSH[@]}" "${USER_NAME}@${HOST}" "
  cd '${REMOTE}'
  [ -f 'data/plans/机械臂分装线.plan' ] && cp 'data/plans/机械臂分装线.plan' 'data/plans/机械臂分装线.plan.bak-${TS}' || true
  for f in arm_driver.py arm_test.py arm_enable.py etherkit_master.py jog_panel.py arm_interface.yaml motion_interface.yaml cit_items.xml components_config.yaml; do
    [ -f \"_internal/scripts/arm/\$f\" ] && cp \"_internal/scripts/arm/\$f\" \"_internal/scripts/arm/\$f.bak-${TS}\" || true
  done
  true
"

"${SCP[@]}" "${ROOT}/data/plans/机械臂分装线.plan" "${USER_NAME}@${HOST}:${REMOTE}/data/plans/机械臂分装线.plan"
"${SCP[@]}" \
  "${ROOT}/scripts/arm/arm_driver.py" \
  "${ROOT}/scripts/arm/arm_test.py" \
  "${ROOT}/scripts/arm/arm_enable.py" \
  "${ROOT}/scripts/arm/etherkit_master.py" \
  "${ROOT}/scripts/arm/jog_panel.py" \
  "${ROOT}/scripts/arm/arm_interface.yaml" \
  "${ROOT}/scripts/arm/motion_interface.yaml" \
  "${ROOT}/scripts/arm/cit_items.xml" \
  "${ROOT}/scripts/arm/components_config.yaml" \
  "${USER_NAME}@${HOST}:${REMOTE}/_internal/scripts/arm/"
"${SCP[@]}" "${ROOT}/data/images/arm_test_right_place.jpg" "${USER_NAME}@${HOST}:${REMOTE}/data/images/arm_test_right_place.jpg"

"${SSH[@]}" "${USER_NAME}@${HOST}" "
  touch '${REMOTE}/_internal/scripts/arm/arm_test.py'
  python3 - <<'PY'
import json
p='${REMOTE}/data/plans/机械臂分装线.plan'
d=json.load(open(p))
for s in d['sequences']:
    if s.get('block_number')=='15085':
        print('15085 cases:', [(c.get('case_no'), c.get('name'), c.get('config',{}).get('function')) for c in s['cases']])
print('sequences:', len(d['sequences']))
PY
"

echo "DEPLOY_OK host=${HOST} backup_ts=${TS}"
