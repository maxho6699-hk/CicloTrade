# CicloTrade strategy sandbox

The main app never executes uploaded code. `runner_service.py` listens only on
`127.0.0.1:8088` and launches one disposable Docker container per submission.

```bash
cd /opt/CicloTrade
docker build -t ciclotrade-sandbox:1 sandbox_runner
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store the generated value in the server secret environment as
`TRADEAI_SANDBOX_TOKEN`. Start the gateway with the same token:

```bash
TRADEAI_SANDBOX_TOKEN='<server-secret>' python3 sandbox_runner/runner_service.py
```

Configure the main app without exposing the service publicly:

```env
TRADEAI_SANDBOX_URL=http://127.0.0.1:8088/run
TRADEAI_SANDBOX_TOKEN=<same-server-secret>
```

For a persistent server process:

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin ciclotrade-sandbox
sudo usermod -aG docker ciclotrade-sandbox
sudo install -d -m 750 /etc/ciclotrade
sudo install -m 640 sandbox_runner/ciclotrade-sandbox.service /etc/systemd/system/ciclotrade-sandbox.service
sudo systemctl daemon-reload
sudo systemctl enable --now ciclotrade-sandbox
```

Create `/etc/ciclotrade/sandbox.env` before starting the unit. It must contain
only `TRADEAI_SANDBOX_TOKEN=<generated-secret>` and be readable by root and the
`docker` group. The main application uses the same value through its own
restricted environment file.

The container has no network, a read-only root filesystem, no Linux
capabilities, 0.5 CPU, 256 MB RAM, 64 PIDs, a five-second execution limit, and
is deleted after each run. Do not bind port 8088 to a public interface.
