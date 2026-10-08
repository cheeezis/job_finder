#!/usr/bin/env bash
# Checks after the rollout: worker and review point to the expected image, and
# the newest review revision runs healthily. Otherwise the run ends with an error. A healthy
# revision may already be asleep again (ScaledToZero); the review scales to zero.
set -euo pipefail
image="$1"
worker=$(az containerapp job show --name jobfinder-worker --resource-group rg-jobfinder \
  --query "properties.template.containers[0].image" -o tsv)
if [ "$worker" != "$image" ]; then
  echo "::error::The worker does not point to the expected image."
  exit 1
fi
for attempt in $(seq 1 20); do
  revision=$(az containerapp show --name jobfinder-review --resource-group rg-jobfinder \
    --query properties.latestRevisionName -o tsv)
  # A here-string instead of process substitution: without a final newline read
  # reports end of file otherwise, and set -e ends the script silently.
  read -r revision_image state health <<< "$(az containerapp revision show --name jobfinder-review \
    --resource-group rg-jobfinder --revision "$revision" \
    --query "[properties.template.containers[0].image, properties.runningState, properties.healthState]" \
    -o tsv | tr '\n' ' ')"
  if [ "$revision_image" = "$image" ] && [ "$health" = "Healthy" ] \
    && case "$state" in Running | RunningAtMaxScale | ScaledToZero) true ;; *) false ;; esac; then
    echo "Review revision $revision runs healthily on the expected image; so does the worker."
    exit 0
  fi
  case "$state" in
    Failed | Degraded)
      echo "::error::Die Review-Revision $revision meldet $state."
      exit 1
      ;;
  esac
  echo "Attempt $attempt: revision $revision is $state/$health, next attempt in 15 seconds"
  sleep 15
done
echo "::error::The new review revision did not become healthy in time."
exit 1
