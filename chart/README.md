# Mithril Helm chart

Deploy [Mithril](https://github.com/AaronGrillot98/mithril) — an LLM firewall — into a Kubernetes cluster.

## Install (from the repo)

```bash
git clone https://github.com/AaronGrillot98/mithril
cd mithril
helm install mithril ./chart
```

## Install with overrides

```bash
helm install mithril ./chart \
  --set config.upstreamUrl=https://api.openai.com/v1 \
  --set config.mode=block \
  --set secrets.judgeApiKey=$OPENAI_API_KEY \
  --set config.judgeEnabled=true
```

## Values

| Key                                | Default                          | Description                                                  |
| ---------------------------------- | -------------------------------- | ------------------------------------------------------------ |
| `image.repository`                 | `ghcr.io/aarongrillot98/mithril` | Container image.                                             |
| `image.tag`                        | `""` (uses `appVersion`)         | Override the tag.                                            |
| `replicas`                         | `1`                              | Number of pod replicas (ignored when autoscaling is enabled). |
| `service.type`                     | `ClusterIP`                      | Service type.                                                |
| `service.port`                     | `8080`                           | Service port.                                                |
| `config.upstreamUrl`               | `https://api.openai.com/v1`      | Where clean requests are forwarded.                          |
| `config.mode`                      | `block`                          | `block` or `log`.                                            |
| `config.threshold`                 | `"0.7"`                          | Detection threshold.                                         |
| `config.judgeEnabled`              | `false`                          | Enable the LLM-judge fallback.                               |
| `config.outputScanEnabled`         | `false`                          | Scan responses for PII / secrets.                            |
| `config.embeddingEnabled`          | `false`                          | Enable semantic-similarity detection.                        |
| `config.metricsEnabled`            | `true`                           | Expose `/metrics`.                                           |
| `secrets.judgeApiKey`              | `""`                             | Judge provider API key (stored in a Secret).                 |
| `persistence.enabled`              | `true`                           | Persist the SQLite event log.                                |
| `persistence.size`                 | `1Gi`                            | PVC size.                                                    |
| `resources`                        | 500m / 256Mi                     | CPU / memory limits.                                         |
| `ingress.enabled`                  | `false`                          | Enable an Ingress resource.                                  |
| `autoscaling.enabled`              | `false`                          | Enable HPA.                                                  |
| `autoscaling.maxReplicas`          | `5`                              | Cap on HPA replicas.                                         |

See `values.yaml` for the full list.

## Upgrading

```bash
helm upgrade mithril ./chart
```

## Uninstall

```bash
helm uninstall mithril
```

The PVC is retained unless you remove it manually:

```bash
kubectl delete pvc mithril-data
```
