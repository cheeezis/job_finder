#!/usr/bin/env bash
# Prüft nach dem Ausrollen: Worker und Review zeigen auf das erwartete Image, und
# die neueste Review-Revision läuft gesund. Sonst endet der Lauf mit Fehler.
set -euo pipefail
image="$1"
worker=$(az containerapp job show --name jobfinder-worker --resource-group rg-jobfinder \
  --query "properties.template.containers[0].image" -o tsv)
if [ "$worker" != "$image" ]; then
  echo "::error::Der Worker zeigt nicht auf das erwartete Image."
  exit 1
fi
for attempt in $(seq 1 20); do
  revision=$(az containerapp show --name jobfinder-review --resource-group rg-jobfinder \
    --query properties.latestRevisionName -o tsv)
  read -r revision_image state health < <(az containerapp revision show --name jobfinder-review \
    --resource-group rg-jobfinder --revision "$revision" \
    --query "[properties.template.containers[0].image, properties.runningState, properties.healthState]" \
    -o tsv | tr '\n' ' ')
  if [ "$revision_image" = "$image" ] && [ "$health" = "Healthy" ] \
    && { [ "$state" = "Running" ] || [ "$state" = "RunningAtMaxScale" ]; }; then
    echo "Review-Revision $revision läuft gesund auf dem erwarteten Image; der Worker ebenso."
    exit 0
  fi
  case "$state" in
    Failed | Degraded)
      echo "::error::Die Review-Revision $revision meldet $state."
      exit 1
      ;;
  esac
  echo "Versuch $attempt: Revision $revision ist $state/$health, neuer Versuch in 15 Sekunden"
  sleep 15
done
echo "::error::Die neue Review-Revision lief nicht rechtzeitig gesund."
exit 1
