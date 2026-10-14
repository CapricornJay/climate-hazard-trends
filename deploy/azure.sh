#!/usr/bin/env bash
# How the live instance was deployed: Azure Container Apps (scale to zero) +
# Azure Cosmos DB for MongoDB (free tier) + a Basic container registry.
# Everything lives in one resource group, so `az group delete -n $RG` removes it.
#
# Needs: az CLI logged in. Run from the repo root.
set -euo pipefail

RG=${RG:-climate-hazards-rg}
LOC=${LOC:-eastus}
SUFFIX=${SUFFIX:?set SUFFIX to something unique, e.g. SUFFIX=$(openssl rand -hex 3)}
COSMOS=cht-cosmos-$SUFFIX
ACR=chtacr$SUFFIX
ENV=cht-env
APP=climate-hazard-trends
TAG=${TAG:-$(git rev-parse --short HEAD)}

az group create -n "$RG" -l "$LOC" -o none

# Free tier = first 1000 RU/s and 25 GB. Server-side retry so bursts get
# queued by Cosmos instead of failing with 16500 errors.
az cosmosdb create -n "$COSMOS" -g "$RG" --kind MongoDB --server-version 7.0 \
  --enable-free-tier true --default-consistency-level Session \
  --locations regionName="$LOC" failoverPriority=0 isZoneRedundant=False \
  --capabilities EnableMongo DisableRateLimitingResponses -o none
# One database with shared throughput so all collections fit in the free 1000 RU/s.
az cosmosdb mongodb database create -a "$COSMOS" -g "$RG" -n climate_hazards --throughput 1000 -o none
MONGODB_URI=$(az cosmosdb keys list -n "$COSMOS" -g "$RG" --type connection-strings \
  --query "connectionStrings[0].connectionString" -o tsv)

az acr create -n "$ACR" -g "$RG" --sku Basic -o none
az acr build -r "$ACR" -t "$APP:$TAG" .   # builds linux/amd64 in Azure

az containerapp env create -n "$ENV" -g "$RG" -l "$LOC" -o none
az containerapp create -n "$APP" -g "$RG" --environment "$ENV" \
  --image "$ACR.azurecr.io/$APP:$TAG" --registry-server "$ACR.azurecr.io" --registry-identity system \
  --ingress external --target-port 8000 \
  --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 1 \
  --secrets "mongodb-uri=$MONGODB_URI" \
  --env-vars MONGODB_URI=secretref:mongodb-uri TRUSTED_PROXY_HOPS=1 -o none

FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
az containerapp update -n "$APP" -g "$RG" --set-env-vars "CORS_ORIGINS=https://$FQDN" -o none
echo "https://$FQDN"

# Later deploys:
#   az acr build -r $ACR -t $APP:$(git rev-parse --short HEAD) .
#   az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/$APP:<tag>
