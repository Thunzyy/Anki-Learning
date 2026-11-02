# Install and run Anki Learning

Use the local launcher or Docker Compose. Both serve the interface and API at **http://127.0.0.1:8700**. Port 5173 is used by the Vite development server.

## Windows

Install Python 3.10+ and Node.js 22.18+ with npm, then clone or unpack the repository. Double-click **start.cmd** or run the launcher from PowerShell:

```powershell
.\start.ps1
```

If PowerShell blocks the script, use `start.cmd`. It applies a process-level exception without changing the permanent execution policy.

## macOS and Linux

Install Python 3.10+ with the `venv` module and Node.js 22.18+ with npm. From the repository root, run:

```sh
sh start.sh
```

Some Linux distributions package `venv` separately, for example as `python3-venv` on Debian and Ubuntu.

## Launcher behavior

On first launch, the script creates `.venv/`, installs Python dependencies, runs `npm ci`, builds the interface, and starts the server. This requires an internet connection. Later runs reuse the installation and repeat the relevant steps when dependencies or frontend sources change.

The browser opens when the API is ready. Keep the terminal open and press **Ctrl+C** to stop the application. The launcher preserves decks and settings in `studio/data/` or the directory set by `LEARNING_DATA_DIR`.

Pass these options after `start.ps1` or `sh start.sh`:

| Option | Effect |
| --- | --- |
| `--install-only` | Install and build without starting the server |
| `--no-browser` | Start without opening a browser |
| `--port 8701` | Use another local port |
| `--reinstall` | Reinstall dependencies and rebuild the interface |

If the port is busy, stop the other instance or choose another port. Give each running instance a separate `LEARNING_DATA_DIR` to avoid simultaneous writes to the same decks.

## Docker Compose

Install Docker with Compose and start the Linux container engine. The container supplies Python and Node.js. From the repository root, run:

```sh
docker compose up -d --build --wait
```

Open **http://127.0.0.1:8700**. The first build downloads base images and dependencies. The health check waits for the API to start.

To inspect logs, stop the service, or start it again:

```sh
docker compose logs -f learning
docker compose stop
docker compose start
```

After updating the source, rebuild with `docker compose up -d --build --wait`.

### Storage and backups

Docker stores decks, settings, media, and exports in the Compose project's named `learning-data` volume. The local launcher uses a separate `studio/data/` directory. Recreating the container preserves the volume. Running `docker compose down -v` deletes it.

Stop writes before copying a backup out of the container:

```sh
docker compose stop
docker compose cp learning:/var/lib/learning ./learning-backup
docker compose start
```

Keep backups private because they may include keys saved in Settings.

### API keys and local sessions

Enter keys in Learning's settings or supply them through environment variables or a `.env` file at the repository root. Git ignores this file and Docker excludes it from the build context. Fill in the values locally:

```dotenv
LEARNING_PORT=8700
OPENAI_API_KEY=
XAI_API_KEY=
```

Compose also forwards `ANTHROPIC_API_KEY`, `UNSPLASH_ACCESS_KEY`, `PEXELS_API_KEY`, `GITHUB_TOKEN`, and `GH_TOKEN` when set.

The container does not inherit your host's CLI sessions. The image omits `claude`, `codex`, `grok`, and `gh`. Use API keys and a GitHub token in Docker, or use the local launcher to reuse compatible CLI sessions on your machine.

### AnkiConnect

To send decks directly to Anki Desktop, install [AnkiConnect](https://ankiweb.net/shared/info/2055492159) through **Tools → Add-ons → Get Add-ons**, enter `2055492159`, and restart Anki.

From Docker, the host address in Learning's settings is usually `http://host.docker.internal:8765`. AnkiConnect must accept connections from Docker. Binding only to `127.0.0.1` may prevent access, depending on Docker networking and your firewall. Alternatively, export an `.apkg` and import it manually in Anki.

### Network access

Compose binds the published port to `127.0.0.1`. Learning has no built-in user authentication. Keep access local or provide authentication in front of the application when hosting it remotely.

## Development

For automatic reloading, run the API and frontend in separate terminals. From the repository root, create the Python environment with `python -m venv .venv`. Activate it with `.\.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` on macOS and Linux.

In the first terminal, install the dependencies and start the API:

```sh
python -m pip install -r studio/api/requirements.txt
python -m uvicorn app.main:app --app-dir studio/api --host 127.0.0.1 --port 8700 --reload --reload-dir studio/api
```

In the second terminal, start at the repository root:

```sh
cd studio/web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Vite forwards API requests to port 8700 by default. The launcher and Docker image set `LEARNING_WEB_DIR` to serve the compiled frontend directly from the API.

Further reading: [Starlette static files](https://www.starlette.io/staticfiles/) and [Docker Compose services](https://docs.docker.com/reference/compose-file/services/).
