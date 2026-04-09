#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   source ./setup_glm_env.sh
#
# Optional overrides before sourcing:
#   export GLM_API_KEY="your-id.secret"
#   export GLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"

export GLM_API_KEY="${GLM_API_KEY:-46e8be63d81b4815b94ba85fbad171eb.sYGkndLdInaGEzP4}"
export GLM_BASE_URL="${GLM_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"

echo "GLM environment configured"
echo "GLM_BASE_URL=${GLM_BASE_URL}"
echo "GLM_API_KEY=${GLM_API_KEY%%.*}.******"
echo
echo "Start viewer with:"
echo "python3 stall_viewer.py --model glm"
