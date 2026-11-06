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
$nextVersion = "v50.7"
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
    DJANGO_ALLOWED_HOSTS="" ^
    DJANGO_CSRF_TRUSTED_ORIGINS="" ^
    ACS_EMAIL_CONNECTION_STRING="endpoint=https://esgrequesthub.asiapacific.communication.azure.com/;accesskey=<your-access-key>" ^
    ACS_EMAIL_SENDER="DoNotReply@dreadops.site" ^
    WEBSITES_PORT="8000"
```

- `WEBSITES_PORT` tells App Service which port the container listens on (Gunicorn binds to 8000).
- Leave `DJANGO_ALLOWED_HOSTS` empty to rely on the automatic `WEBSITE_HOSTNAME` detection added in `settings.py`. Provide extra hosts if needed (comma-separated).
- When `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, `DB_HOST`, and `DB_PASSWORD` are required or the app fails closed at boot.
- If you connect to PostgreSQL, provide `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` values here as well. Django 6.1 requires PostgreSQL 15+.
- App Service already forwards `X-Forwarded-Proto`; production settings trust `HTTP_X_FORWARDED_PROTO=https`.
- `ACS_EMAIL_CONNECTION_STRING` should point at the Request Hub Azure Communication Services endpoint `https://esgrequesthub.asiapacific.communication.azure.com`.
- `ACS_EMAIL_SENDER` should be the verified sender address `DoNotReply@dreadops.site`.

## 4. Optional: Configure Startup Commands

The image collects and compresses static files during the Docker build, using a build-only development configuration that does not persist into the running container. The image defaults `RUN_COLLECTSTATIC=false`; leave it disabled unless an operator deliberately needs to regenerate assets. Production should retain `RUN_MIGRATIONS=false` and use the controlled release migration step. The entrypoint then starts Gunicorn; no additional App Service startup command is required.

### Performance configuration (requires separate operational approval)

- Enable **Always On** to prevent idle unloads. It does not eliminate deployment/restart warm-up.
- Configure the App Service health-check path as `/readyz/`. `/healthz/` is database-free liveness; `/readyz/` verifies database connectivity with `SELECT 1` and returns a generic 503 on failure.
- Keep the current App Service/PostgreSQL sizes until request-level and peak-period measurements justify a capacity change.
- For temporary staging diagnostics, set `DJANGO_PAGE_PERFORMANCE_ENABLED=true` and `DJANGO_ENVIRONMENT=staging`. Development also allows the diagnostic flag with `DJANGO_DEBUG=true`. Diagnostics are disabled by default and remain disabled in production unless explicitly classified as staging.
- Enabled diagnostics emit a `Server-Timing` header and sanitized `hub.performance` log records containing route name, response status, elapsed time, SQL execution time and query count. They do not log SQL, parameters, identities, or cookies. Database duration is included in request duration; do not add them together. Timings cover the synchronous response, not subsequent streaming iteration.
- Runtime changelog synchronization is still bounded by its existing timeout. Moving it to a release step requires a separately verified database-access and failure-recovery path; do not simply disable it.

### Optional Application Insights tracing

The application includes opt-in request and database timing using the Azure
Monitor OpenTelemetry exporter. It is disabled by default. Before enabling it,
configure a system-assigned managed identity for the app and grant it the
**Monitoring Metrics Publisher** role scoped to the Application Insights
resource; the existing App Service does not currently have an identity. Set
`APPLICATIONINSIGHTS_CONNECTION_STRING` to the full existing component
connection string, including the ingestion endpoint, and set
`AZURE_TELEMETRY_ENABLED=true`. Missing or invalid configuration is an error.
Keep Gunicorn's default non-preloaded configuration so each worker initializes
its own exporter after forking.

Request spans contain a resolved view name, an allowlisted HTTP method, status,
and timing. Database spans contain the database type and execution duration;
they do not include SQL or parameters. Health and readiness probes are excluded.
The pipeline does not instrument outbound HTTP, email, AI calls, or application
logs, and does not include raw paths, query strings, headers, request bodies, or
exception messages. Offline exporter storage is disabled. Sampling currently
records every trace, so measure ingestion volume, cost and request overhead in
staging before enabling production. Streaming response iteration is not timed.
The Azure Monitor exporter dependency is currently a beta release.

To disable tracing, set `AZURE_TELEMETRY_ENABLED=false` and restart the app.
Verify requests and database dependencies arrive in the intended workspace after
the approved deployment; workspace relinking alone does not instrument Django.

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

- Production uses Azure Database for PostgreSQL Flexible Server (PostgreSQL 15+ for Django 6.1).
- Expose credentials through app settings (`DB_HOST` and `DB_PASSWORD` are required when `DJANGO_DEBUG=False`) and ensure outbound access via VNet integration or public firewall rules.
- Run migrations manually if you disable automatic migrations in `entrypoint.sh`.

With these steps, the Dockerized Request Hub application runs on Azure App Service with environment-specific configuration provided through Azure settings.
