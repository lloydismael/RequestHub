# Local development

Use **Python 3.12**. VS Code runs Django directly on Windows; Docker is optional and only used for one local PostgreSQL setup. Run these commands from the repository root.

## Install dependencies

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock -r requirements-windows.txt
```

### macOS and Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
```

## Run with live Azure PostgreSQL (no Docker)

In **Run and Debug**, select **Django: Request Hub (auto-reload)** and press **F5**. Stop an existing debug session first if it is using port 8000. The app runs at `127.0.0.1:8000` using the existing Azure PostgreSQL database; this profile does not create Azure resources or use Docker. It reads `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, optional `DB_PORT`, and optional `DB_SSLROOTCERT` from the ignored `.env`. It verifies the Azure hostname and TLS certificate using the configured CA file or the `certifi` trust store. It does not import deployment email, telemetry, or signing settings from that file.

Startup and each code reload check migration history and required model columns in a read-only transaction. The debugger never applies migrations. Once the server starts, **sign-in and other app actions can write to production**. Use an isolated database for testing, and never run tests or `manage.py migrate` with live credentials.

If the live schema is behind your checkout, the same profile can temporarily run a verified release snapshot. When `.venv\request-hub-f5\release.json` is present, the launcher uses the Git export under that directory named by the manifest's `revision`, with the manifest's explicitly verified extra migration records for that revision. Credentials still come from the workspace `.env`. Auto-reload watches the running copy, so edits in the main checkout do not appear in this session. The [local release endpoint](http://127.0.0.1:8000/release/) reports the running revision.

Once the live schema and migration history match the checkout, stop debugging, remove the local `release.json`, and press F5 to run the checkout again. The normal release workflow, not the debugger, applies any required migration.

## Configure an isolated development database (optional)

Use any PostgreSQL instance listening on loopback with the required development database and user. On Windows with Docker Desktop, start the existing development container with `docker start request-hub-vscode-postgres`. For a fresh Docker setup, choose a local-only password and run the following once:

```powershell
$password = Read-Host 'Choose a local PostgreSQL password' -AsSecureString
$env:POSTGRES_PASSWORD = [System.Net.NetworkCredential]::new('', $password).Password
try {
    docker run --detach --name request-hub-vscode-postgres --restart unless-stopped `
        --publish 127.0.0.1:55432:5432 --volume request-hub-vscode-pgdata:/var/lib/postgresql/data `
        --env POSTGRES_DB=request_hub_local --env POSTGRES_USER=request_hub_dev --env POSTGRES_PASSWORD postgres:16-alpine
} finally {
    Remove-Item Env:POSTGRES_PASSWORD
}
```

Create an ignored `.env.local` with these settings, or keep the file already provisioned for this checkout. Use the same database name, role, and password chosen when the database was created. Generate a local Django signing key with `python -c "import secrets; print(secrets.token_hex(32))"` and keep it on one line. Do not copy deployment settings into this file.

```dotenv
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=<generated local key>
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_PUBLIC_BASE_URL=http://127.0.0.1:8000
DB_HOST=127.0.0.1
DB_PORT=55432
DB_NAME=request_hub_local
DB_USER=request_hub_dev
DB_PASSWORD=<local database password>
DB_SSLMODE=disable
```

Use `DB_SSLMODE=disable` only for a loopback database without TLS. Do not store application sign-in passwords in `.env.local`.

## Apply local migrations and start Django

From the activated virtual environment, explicitly load `.env.local` before running management commands. The following checks the database address before invoking Django and prevents automatic loading of `.env`:

```shell
python -c "import os, runpy; from dotenv import dotenv_values; config = dotenv_values('.env.local', interpolate=False); required = ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_PORT', 'DJANGO_SECRET_KEY'); config.get('DB_HOST') == '127.0.0.1' and all(config.get(key) for key in required) or exit('Configure .env.local first'); os.environ.update(config); os.environ['PYTHON_DOTENV_DISABLED'] = '1'; runpy.run_path('manage.py', run_name='__main__')" migrate
```

For isolated development in VS Code on Windows, select **Django: Request Hub** (without **auto-reload**) and press **F5**. It loads `.env.local`, pins PostgreSQL to `127.0.0.1`, and leaves the deployment `.env` untouched. To start outside VS Code, replace `migrate` at the end of the command with `runserver 127.0.0.1:8000`. If a local server is already listening on port 8000, use it or stop it before launching another copy.

Open <http://127.0.0.1:8000/>. Check <http://127.0.0.1:8000/readyz/> if sign-in is unavailable, and rerun local migrations after pulling schema changes. The supplied launch configurations show **App Version dev** when `APP_VERSION` is unset. Restart Django after changing the local environment.

## Project structure

- [accounts/](../accounts/) owns sign-in, account access, and profiles. [hub/](../hub/) owns requests, reporting, activity, and quotations.
- Add request endpoints in the relevant [view module](../hub/views/), preserve existing imports through the [view facade](../hub/views/__init__.py), and keep business rules in [services](../hub/services/).
- Shared presentation lives in [templates/](../templates/) and [static/](../static/). Regression suites live in the Django apps and [tests/](../tests/); operational tooling lives in [scripts/](../scripts/).

## Maintainer references

- [Environment variable template](../.env.example) and [container configuration](../docker-compose.yml). Compose starts the web service; it requires a separately reachable development PostgreSQL instance.
- [Azure deployment and app settings](azure-app-service-deployment.md) and [CI validation](ci-workflow-testing.md).
- [Browser test fixtures](unreleased-responsive-navbar-changelog.md#local-browser-checks) and [release changelog workflow](cd-workflow-testing.md#release-changelog).
