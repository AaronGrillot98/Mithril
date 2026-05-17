# Production deployment

This guide covers running Mithril as a production gateway in front of one or more LLM providers. The proxy is stateless (modulo the SQLite event log), so deployment is mostly about wiring up secrets, persistence, observability, and the same boring operational controls you'd put in front of any HTTP service.

> **TL;DR:** pick Helm if you're on Kubernetes; pick Docker Compose for a single host; pick the pip install if you're embedding the proxy alongside another Python service. In every case, set `MITHRIL_MODE=block`, lock down `/metrics` and `/api/events`, mount a persistent volume for the event log, and front the proxy with TLS termination.

## Table of contents

1. [Choosing a deployment path](#choosing-a-deployment-path)
2. [Docker Compose (single host)](#docker-compose-single-host)
3. [Kubernetes via Helm](#kubernetes-via-helm)
4. [Bare-metal / systemd](#bare-metal--systemd)
5. [Hardening checklist](#hardening-checklist)
6. [Observability](#observability)
7. [Scaling and high availability](#scaling-and-high-availability)
8. [Upgrades](#upgrades)
9. [Incident response](#incident-response)

---

## Choosing a deployment path

| Path                       | Best for                                                    | Effort     | HA      |
| -------------------------- | ----------------------------------------------------------- | ---------- | ------- |
| **Docker Compose**         | One host, a handful of clients, a quick PoC                 | 10 minutes | Single point of failure |
| **Helm on Kubernetes**     | Multi-tenant clusters, autoscaling, GitOps                  | 30 minutes | Yes     |
| **Bare metal / systemd**   | Existing Python deployments, air-gapped sites               | 20 minutes | Manual  |
| **Serverless (Lambda/Run)** | Spiky traffic, no event log requirement                    | 1 hour     | Yes, but cold starts hurt judge latency |

Mithril is a stateless HTTP service except for the SQLite event log. That log is append-only and crash-safe (WAL mode) but is *not* shared across replicas. If you run more than one pod, each replica writes to its own log; for a single audit trail, either pin to one replica or migrate the event log to an external store (roadmap).

---

## Docker Compose (single host)

The repo ships a `docker-compose.yml` ready for a small deployment. Copy it, fill in secrets via a `.env` file, and run:

```bash
git clone https://github.com/AaronGrillot98/mithril
cd mithril

cat > .env <<'EOF'
MITHRIL_UPSTREAM_URL=https://api.openai.com/v1
MITHRIL_MODE=block
MITHRIL_THRESHOLD=0.7
MITHRIL_JUDGE_ENABLED=true
MITHRIL_JUDGE_API_KEY=sk-...
MITHRIL_JUDGE_MODEL=gpt-4o-mini
MITHRIL_OUTPUT_SCAN_ENABLED=true
MITHRIL_METRICS_ENABLED=true
EOF

docker compose up -d
```

Verify:

```bash
curl http://localhost:8080/health
curl -X POST http://localhost:8080/v1/scan -H 'content-type: application/json' \
  -d '{"text":"hello"}'
```

The `mithril-data` named volume persists the SQLite event log across container restarts.

For production use add at minimum:

- A reverse proxy (nginx / Caddy / Traefik) terminating TLS in front of `:8080`.
- Resource limits in the compose service (`mem_limit: 512m`, `cpus: '1.0'`).
- Log driver pointed at your aggregator (`logging.driver: gelf`, etc.).

---

## Kubernetes via Helm

The Helm chart lives in `chart/`. Minimal install:

```bash
helm install mithril ./chart \
  --namespace mithril --create-namespace \
  --set config.upstreamUrl=https://api.openai.com/v1 \
  --set config.mode=block \
  --set config.judgeEnabled=true \
  --set secrets.judgeApiKey=$OPENAI_API_KEY
```

Production install with autoscaling, ingress, and external-secrets:

```yaml
# values.prod.yaml
image:
  tag: "0.6.0"

config:
  upstreamUrl: https://api.openai.com/v1
  mode: block
  threshold: "0.85"
  judgeEnabled: true
  outputScanEnabled: true
  embeddingEnabled: false
  metricsEnabled: true

# Secret content is injected by External Secrets Operator from your vault.
secrets:
  judgeApiKey: ""  # populated by ExternalSecret

persistence:
  enabled: true
  size: 5Gi
  storageClass: gp3

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: 4m
  hosts:
    - host: mithril.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - hosts: [mithril.example.com]
      secretName: mithril-tls

resources:
  limits:
    cpu: 1
    memory: 512Mi
  requests:
    cpu: 250m
    memory: 256Mi
```

```bash
helm upgrade --install mithril ./chart \
  --namespace mithril --create-namespace \
  -f values.prod.yaml
```

### Secret management

The chart ships a basic `Secret` template that holds the judge API key. Replace it with one of:

- **External Secrets Operator** referencing AWS Secrets Manager / GCP Secret Manager / Vault.
- **SOPS-encrypted secret** committed alongside the values file.
- **Sealed Secrets** so the encrypted blob can live in Git.

Pass `--set secrets.judgeApiKey=""` and create the secret out-of-band; the chart's `envFrom` already references it by name.

---

## Bare-metal / systemd

Install from PyPI into a virtualenv owned by an unprivileged user:

```bash
sudo useradd --system --home /var/lib/mithril --shell /usr/sbin/nologin mithril
sudo -u mithril python3 -m venv /var/lib/mithril/venv
sudo -u mithril /var/lib/mithril/venv/bin/pip install --upgrade pip
sudo -u mithril /var/lib/mithril/venv/bin/pip install 'mithril-llm[embeddings]'
```

Drop a unit at `/etc/systemd/system/mithril.service`:

```ini
[Unit]
Description=Mithril LLM firewall
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mithril
Group=mithril
WorkingDirectory=/var/lib/mithril
EnvironmentFile=/etc/mithril/mithril.env
ExecStart=/var/lib/mithril/venv/bin/mithril serve
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mithril
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Put environment variables in `/etc/mithril/mithril.env` (mode 0640, owner `root:mithril`):

```ini
MITHRIL_UPSTREAM_URL=https://api.openai.com/v1
MITHRIL_MODE=block
MITHRIL_THRESHOLD=0.7
MITHRIL_DB_PATH=/var/lib/mithril/mithril.db
MITHRIL_JUDGE_ENABLED=true
MITHRIL_JUDGE_API_KEY=sk-...
MITHRIL_METRICS_ENABLED=true
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mithril
journalctl -u mithril -f
```

---

## Hardening checklist

A short list of choices that matter once the proxy is in front of real traffic.

- [ ] **Mode is `block`, not `log`.** The default is `block`; `log` is for staged rollouts only.
- [ ] **Threshold is tuned to your traffic.** Run `scripts/benchmark.py` and `scripts/jailbreakbench_eval.py` against a sample of *your* prompts; the default of 0.7 may be too eager for high-volume support chat.
- [ ] **Output scanning is enabled** if model responses leave your boundary (customer-facing chat, public APIs). Set `MITHRIL_OUTPUT_SCAN_ENABLED=true` and choose `block` or `redact` per your tolerance.
- [ ] **Body and response size caps** match your expected ceilings (`MITHRIL_MAX_BODY_BYTES`, `MITHRIL_MAX_RESPONSE_BYTES`). The defaults (1 MiB / 4 MiB) are generous; consider tightening.
- [ ] **The proxy itself is authenticated.** Mithril does not enforce client auth. Front it with an ingress that requires an API key / mTLS / OAuth, or run inside a private network and rely on network policy.
- [ ] **Upstream uses TLS.** Set `MITHRIL_UPSTREAM_URL=https://...` even for in-cluster providers.
- [ ] **Judge API keys are rotated.** Treat `MITHRIL_JUDGE_API_KEY` like any other production secret — rotate quarterly at minimum, sooner on staff departures.
- [ ] **The SQLite event log is on a persistent volume** and included in your backup policy. Losing it means losing the audit trail.
- [ ] **The dashboard `/` route and `/api/events` are gated** behind ingress auth — they expose recent prompt snippets.
- [ ] **`/metrics` is not exposed publicly.** Scrape it from inside the cluster only; set `MITHRIL_METRICS_ENABLED=false` if you can't restrict by network policy.
- [ ] **Pin to a minor version** (`mithril-llm==0.6.*`) so a same-minor bug-fix release picks up automatically but a breaking-change minor needs an opt-in.

---

## Observability

### Prometheus

Mithril ships a `/metrics` endpoint (default-on). Alongside the standard FastAPI HTTP histograms, it exposes:

| Metric                              | Type      | Use                                                |
| ----------------------------------- | --------- | -------------------------------------------------- |
| `mithril_blocked_total`             | counter   | Volume of blocked prompts by severity / rule.      |
| `mithril_allowed_total`             | counter   | Volume of clean prompts.                           |
| `mithril_scan_duration_seconds`     | histogram | Detection-pipeline latency budget.                 |
| `mithril_judge_calls_total`         | counter   | Judge invocation rate by verdict.                  |
| `mithril_output_blocked_total`      | counter   | Output scanner activity by mode and severity.      |
| `mithril_event_log_writes_total`    | counter   | Backpressure indicator for the SQLite log.         |

Sample Prometheus scrape:

```yaml
scrape_configs:
  - job_name: mithril
    metrics_path: /metrics
    static_configs:
      - targets: ['mithril.mithril.svc.cluster.local:8080']
```

A starter Grafana dashboard (PromQL) — drop these into a single dashboard:

```promql
# Block rate (5m)
sum(rate(mithril_blocked_total[5m]))

# p95 scan latency
histogram_quantile(0.95, sum(rate(mithril_scan_duration_seconds_bucket[5m])) by (le))

# Judge verdict mix
sum by (verdict) (rate(mithril_judge_calls_total[5m]))

# Output scanner activity
sum by (mode, severity) (rate(mithril_output_blocked_total[5m]))
```

### Structured logs

Mithril emits one structured access log line per request via the `mithril.access` logger. Ship those to your aggregator (Loki / Elastic / Datadog) and alert on:

- `level=warning` events from `mithril.server` (upstream failures, body-size breaches).
- `action=block` event-log rows landing in the SQLite log — useful for tracking trends per-route or per-tenant.

### Event log

The SQLite event log is the canonical audit record. Each row has the action (`block` / `allow` / `log`), the top severity, the score, every finding (as JSON), and the first 500 chars of the prompt. Use `mithril events --limit 200` from the CLI to inspect recent activity, or query the file directly with `sqlite3 mithril.db`.

---

## Scaling and high availability

The proxy itself is CPU- and IO-bound, not memory-bound. A single replica handles thousands of requests per second on a 1 vCPU box when the judge is disabled. With the judge on, the bottleneck is the judge's own latency (typically 200 – 800 ms per ambiguous prompt).

### Replicas

Stateless except for the event log:

- **Multiple replicas, no shared log:** each pod writes its own log. Acceptable for fleets where audit data is aggregated downstream (log shipping → S3 → query engine).
- **One replica with HPA at min=1, max=1:** simplest; the proxy is fast enough that a single replica handles most teams.
- **Multiple replicas with a shared backend:** on the roadmap (Postgres). For now, ship SQLite logs out of each pod and aggregate centrally.

### Cost notes

- The regex/embedding layers are nearly free.
- The judge layer dominates cost. A blended cost of $0.0001 – $0.0005 per ambiguous prompt is typical with `gpt-4o-mini` as the judge. Pin `MITHRIL_JUDGE_LOW_THRESHOLD` and `MITHRIL_JUDGE_HIGH_THRESHOLD` to a narrow band to keep judge call volume down.
- Output scanning adds zero per-token cost but does buffer up to `MITHRIL_MAX_RESPONSE_BYTES` in `redact` mode. Use `incremental` streaming mode for `block`/`log`.

---

## Upgrades

1. **Pin to a minor:** `mithril-llm==0.6.*` (or `appVersion: "0.6.0"` in your Helm values).
2. **Watch `CHANGELOG.md`** for the next minor's breaking-change list. Mithril follows semantic versioning; breaking changes only land in minor bumps while we're pre-1.0.
3. **Canary before fleet:** roll the new version to one replica behind a fraction of traffic, watch `mithril_blocked_total` and the false-positive issue tracker for 24h, then proceed.
4. **Re-run your tuned threshold benchmark** after every minor bump — detector additions can shift the score distribution.

---

## Incident response

When the proxy starts blocking traffic that you expect to flow:

1. **Inspect the event log:**

   ```bash
   mithril events --action block --limit 50
   ```

   Each row shows the rule that fired, its confidence, and a snippet of the offending prompt.

2. **Replay through the standalone scan endpoint:**

   ```bash
   curl -X POST http://mithril:8080/v1/scan \
     -H 'content-type: application/json' \
     -d '{"text": "the prompt that got blocked"}'
   ```

   Use this to confirm whether it's a true positive, a false positive, or judge disagreement.

3. **Lower the threshold for that route** as a temporary fix while you triage. Routes can be split with multiple proxy instances if you need different thresholds per traffic class.

4. **Open a false-positive issue** with the prompt and rule ID. Mithril rule IDs are stable across releases, so the maintainers can ship a targeted fix without rewriting the rule set.

5. **For a confirmed novel attack pattern** that slipped through, open a new-attack-pattern issue with a minimal reproducer. New rules are usually a single regex with a test case in `tests/test_detectors.py`.

When the proxy itself starts misbehaving (5xx rate spikes, upstream timeouts):

- `/health` is your liveness probe. If it returns 200 but traffic still fails, the upstream is sick — check `mithril_judge_calls_total{verdict="error"}` and the access log for `upstream forward_chat failed` warnings.
- `mithril_scan_duration_seconds` p95 climbing is usually the judge slowing down; consider lowering its timeout (`MITHRIL_JUDGE_TIMEOUT`) and trusting `fail_mode=open` to keep traffic flowing.
- A growing SQLite event log can slow writes; rotate the DB file weekly (`mv mithril.db mithril.db.$(date +%F); systemctl restart mithril`).

---

For end-to-end framework integration (LangChain, LiteLLM, FastAPI), see the integration guides in [`docs/`](.).
