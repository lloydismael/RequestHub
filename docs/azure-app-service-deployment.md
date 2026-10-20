# Deploying Request Hub to Azure App Service (Linux Container)

This guide walks through running the Dockerized Request Hub Django app on Azure App Service.

## Prerequisites

- Azure subscription with permission to create resource groups and App Service plans
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) 2.55+ installed and signed in (`az login`)
- Docker installed locally (only needed if you plan to build/push images yourself)
- Request Hub image pushed to a registry (Docker Hub is used below)

## 1. Choose or Publish a Container Image

If you have changes locally, rebuild and push the image:

```powershell
# From the project root
$env:DOCKER_BUILDKIT=1
# Inspect existing tags to avoid duplicates
docker images lloydismael12/request-hub --format "{{.Tag}}" | sort
# Update this to the next unused version.
# Patch tags are single digit only: v50.0 through v50.9, then v51.0.
$nextVersion = "v51.1"
$tag = "lloydismael12/request-hub:$nextVersion"
docker build --pull --no-cache --build-arg APP_VERSION=$nextVersion -t $tag .
docker scout cves $tag
docker push $tag
# Do not retag or push latest until CVE scans on this immutable tag are clean.
```

> App Service will pull the tagged image directly from Docker Hub. Replace the registry path if you use Azure Container Registry (ACR). Do not create tags such as `v48.10`; after `v48.9`, roll over to `v49.0`. Only retag `latest` after the immutable version tag scans clean.

## 2. Azure Resource Setup

```powershell
$RESOURCE_GROUP = "rg-request-hub"
$PLAN = "plan-request-hub"
$APP = "request-hub-web"
$LOCATION = "southeastasia"  # choose the region closest to your users

az group create --name $RESOURCE_GROUP --location $LOCATION

# Create a Linux App Service plan (SKU B1 = Basic). Adjust size as needed.
az appservice plan create ^
  --name $PLAN ^
  --resource-group $RESOURCE_GROUP ^
  --is-linux ^
  --sku B1

# Create the web app bound to the container image on Docker Hub
az webapp create ^
  --name $APP ^
  --resource-group $RESOURCE_GROUP ^
  --plan $PLAN ^
  --deployment-container-image-name lloydismael12/request-hub:$nextVersion
```

If you prefer using ACR:
1. `az acr create ...`
2. Push the image to ACR.
3. Supply `--deployment-container-image-name <acrLoginServer>/request-hub:$nextVersion` and configure registry credentials via `az webapp config container set`.

## 3. Configure App Settings

Azure passes settings to the container as environment variables. Set the following (adjust values to your environment):

```powershell
az webapp config appsettings set ^
  --name $APP ^
  --resource-group $RESOURCE_GROUP ^
  --settings ^
    DJANGO_SECRET_KEY="$(New-Guid)" ^
    DJANGO_DEBUG="False" ^
    DJANGO_PRIMARY_DOMAIN="esgrequesthub.com" ^
    DJANGO_PUBLIC_BASE_URL="https://esgrequesthub.com" ^
    DJANGO_ALLOWED_HOSTS="esgrequesthub.com" ^
    DJANGO_CSRF_TRUSTED_ORIGINS="https://esgrequesthub.com" ^
    DJANGO_DEFAULT_FROM_EMAIL="ESG Request Hub <no-reply@esgrequesthub.com>" ^
    ACS_EMAIL_CONNECTION_STRING="endpoint=https://esgrequesthub.asiapacific.communication.azure.com/;accesskey=<your-access-key>" ^
    ACS_EMAIL_SENDER="DoNotReply@esgrequesthub.com" ^
    WEBSITES_PORT="8000"
```

- `WEBSITES_PORT` tells App Service which port the container listens on (Gunicorn binds to 8000).
- `DJANGO_PRIMARY_DOMAIN` is `esgrequesthub.com`. Each comma-separated entry is auto-added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`; do not include retired hosts.
- `DJANGO_PUBLIC_BASE_URL` controls outbound assignment, SQR notification, and closing email links, including notifications sent without an HTTP request. Use `https://esgrequesthub.com`; do not infer this URL from the incoming host.
- Set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to the apex values above. `WEBSITE_HOSTNAME` remains automatically allowed for Azure health checks. Only add `www` after its DNS, hostname binding, and TLS are verified.
- When `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, `DB_HOST`, and `DB_PASSWORD` are required or the app fails closed at boot.
- If you connect to PostgreSQL, provide `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` values here as well. Django 5.2 requires PostgreSQL 14+.
- App Service already forwards `X-Forwarded-Proto`; production settings trust `HTTP_X_FORWARDED_PROTO=https`.
- `ACS_EMAIL_CONNECTION_STRING` should point at the Request Hub Azure Communication Services endpoint `https://esgrequesthub.asiapacific.communication.azure.com`.
- `ACS_EMAIL_SENDER` must be a verified sender on the `esgrequesthub.com` email domain: `DoNotReply@esgrequesthub.com`.
- Custom domains: bind `esgrequesthub.com` (apex `A` + `TXT` verification) and its TLS certificate. `www` is optional, not required for notification links.

### Retiring a previous domain

1. Inventory production settings, slots, hostname/TLS bindings, DNS records, ACS linked domains, and other consumers. Save a secure rollback snapshot without putting secrets in the repository.
2. Deploy the canonical email-link changes and apex settings above. Verify HTTPS, login/CSRF, request submission, assignment, SQR notifications, closing drafts, and actual email delivery before destructive changes.
3. Remove only the retired application's DNS records (including dedicated verification records), allowing the previous TTL to elapse, then unbind its App Service hostname. Remove its dedicated certificate only after checking that no other binding uses it. Existing bookmarks and historical email links to the retired host will stop working.
4. Unlink/remove the old ACS email domain only after confirming every consumer uses the verified replacement domain. A parent-domain email resource may serve other applications; preserve it unless its ownership and lack of other consumers are confirmed.
5. Preserve the primary-domain DNS/TLS/email records, shared ACS service, parent DNS zone, and all application data/history. Recheck the primary URL and sender, monitor errors, and retain the rollback snapshot; rollback requires continued ownership of the retired domain.

## 4. Optional: Configure Startup Commands

The container’s `CMD` already runs the entrypoint which collects static files, migrates the database, and starts Gunicorn. No additional App Service startup command is required.

## 5. Restart and Verify

```powershell
az webapp restart --name $APP --resource-group $RESOURCE_GROUP
az webapp browse --name $APP --resource-group $RESOURCE_GROUP
```

The browse command launches the app in your default browser. You can inspect logs via:

```powershell
az webapp log config --name $APP --resource-group $RESOURCE_GROUP --docker-container-logging filesystem
az webapp log tail --name $APP --resource-group $RESOURCE_GROUP
```

## 6. Ongoing Updates

1. Rebuild the Docker image with the next tag in sequence, using `--pull --no-cache` so patched base layers are included.
2. Scan the image with Docker Scout or Trivy and resolve fixable HIGH/CRITICAL findings before pushing.
3. Push the immutable version tag, then point App Service to the new tag:

```powershell
az webapp config container set ^
  --name $APP ^
  --resource-group $RESOURCE_GROUP ^
  --docker-custom-image-name lloydismael12/request-hub:$nextVersion
az webapp restart --name $APP --resource-group $RESOURCE_GROUP
```

4. Continue incrementing patch versions only up to `.9` for each major/minor cycle; for example, `v48.9` rolls over to `v49.0`. Do not use `v48.10` or higher.
5. The `Container Security` GitHub Actions workflow audits locked Python dependencies, scans the built image, and uploads an SBOM artifact for each relevant PR/push.

## Notes on Database Connectivity

- Production uses Azure Database for PostgreSQL Flexible Server (PostgreSQL 14+ for Django 5.2).
- Expose credentials through app settings (`DB_HOST` and `DB_PASSWORD` are required when `DJANGO_DEBUG=False`) and ensure outbound access via VNet integration or public firewall rules.
- Run migrations manually if you disable automatic migrations in `entrypoint.sh`.

With these steps, the Dockerized Request Hub application runs on Azure App Service with environment-specific configuration provided through Azure settings.
