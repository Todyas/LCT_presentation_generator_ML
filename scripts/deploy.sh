#!/usr/bin/env bash
set -Eeuo pipefail

if (($# != 3)); then
  echo "Usage: $0 <backend-image> <frontend-image> <image-tag>" >&2
  exit 2
fi

backend_image=$1
frontend_image=$2
image_tag=$3
compose=(docker compose)

previous_image=""
previous_frontend_image=""
app_container="$("${compose[@]}" ps -q app 2>/dev/null || true)"
frontend_container="$("${compose[@]}" ps -q frontend 2>/dev/null || true)"
if [[ -n "$app_container" ]]; then
  previous_image="$(docker inspect --format '{{.Config.Image}}' "$app_container" 2>/dev/null || true)"
fi
if [[ -n "$frontend_container" ]]; then
  previous_frontend_image="$(docker inspect --format '{{.Config.Image}}' "$frontend_container" 2>/dev/null || true)"
fi

rollback() {
  local exit_code=$?

  trap - ERR

  if [[ -z "$previous_image" || "$previous_image" != *:* ]]; then
    echo "Deployment failed and there is no previous image to roll back to." >&2
    exit "$exit_code"
  fi

  echo "Deployment failed; rolling back to $previous_image" >&2
  if [[ -n "$previous_frontend_image" && "$previous_frontend_image" == *:* ]]; then
    BACKEND_IMAGE="${previous_image%:*}" \
      FRONTEND_IMAGE="${previous_frontend_image%:*}" \
      IMAGE_TAG="${previous_image##*:}" \
      "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 app worker frontend
  else
    BACKEND_IMAGE="${previous_image%:*}" IMAGE_TAG="${previous_image##*:}" \
      "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 app worker
  fi
  exit "$exit_code"
}
trap rollback ERR

export BACKEND_IMAGE="$backend_image"
export FRONTEND_IMAGE="$frontend_image"
export IMAGE_TAG="$image_tag"

"${compose[@]}" pull app worker migrate init-storage frontend
"${compose[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 180

trap - ERR
echo "Successfully deployed ${BACKEND_IMAGE}:${IMAGE_TAG}"
