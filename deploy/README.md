# Sentigon V2 — Kubernetes / RunPod deploy

The Helm chart in [`helm/sentigon`](helm/sentigon) deploys the full application
plane (API, ingest, context, search, notify, media-source, MCP), the GPU
perception worker, the vLLM Qwen3-VL reasoning tier, and the Next.js console.
Data services (Postgres, Redis, Kafka/Redpanda, Qdrant, MinIO) are treated as
**external**: point `infra.*` at managed endpoints or at in-cluster deployments
you run separately.

> Status: the chart is authored and validated locally with `helm lint` and
> `helm template` (manifests render clean). A live `helm install` needs a
> Kubernetes cluster with GPU nodes (A100/H100) plus a container registry to
> push the images to; that hardware is not available on the dev box, so the
> apply step is documented but not exercised here.

## 1. Build and push images

```bash
# Python services (all-in-one image; also serves the GPU perception worker)
docker build -f deploy/docker/Dockerfile      -t $REGISTRY/sentigon:0.1.0     .
docker tag  $REGISTRY/sentigon:0.1.0            $REGISTRY/sentigon-gpu:0.1.0
# Console
docker build -f deploy/docker/Dockerfile.web  -t $REGISTRY/sentigon-web:0.1.0 .

docker push $REGISTRY/sentigon:0.1.0
docker push $REGISTRY/sentigon-gpu:0.1.0
docker push $REGISTRY/sentigon-web:0.1.0
```

## 2. Provision the cluster

- GPU nodes with the **NVIDIA device plugin** (advertises `nvidia.com/gpu`).
- On RunPod, create a GPU pool (A100 80GB or H100); label/taint it so the
  perception + vLLM pods land there. `values-runpod.yaml` shows the selector
  and toleration to match.
- Managed or self-hosted Postgres 16, Redis 7, a Kafka API (Redpanda), Qdrant,
  and MinIO/S3. Put their URLs in your values override.

## 3. Install

```bash
helm install sentigon deploy/helm/sentigon \
  -n sentigon --create-namespace \
  -f deploy/helm/sentigon/values-runpod.yaml \
  --set image.repository=$REGISTRY/sentigon \
  --set perception.image.repository=$REGISTRY/sentigon-gpu \
  --set web.image.repository=$REGISTRY/sentigon-web \
  --set infra.databaseUrl="postgresql+asyncpg://USER:PW@HOST:5432/sentigon" \
  --set infra.kafkaBootstrap="HOST:9092" \
  --set infra.qdrantUrl="http://HOST:6333" \
  --set infra.minioEndpoint="HOST:9000" \
  --set secrets.jwtSecret="$(openssl rand -hex 32)" \
  --set secrets.serviceToken="$(openssl rand -hex 32)" \
  --set secrets.adminPassword="$ADMIN_PW" \
  --set vllm.hfToken="$HF_TOKEN"
```

The post-install Job runs `alembic upgrade head` and seeds the governed
ontology + signature defaults + the single admin credential (config only, no
runtime data).

## 4. Verify

```bash
kubectl -n sentigon get pods
kubectl -n sentigon logs job/sentigon-migrate
kubectl -n sentigon port-forward svc/sentigon-web 3000:3000
# then open http://localhost:3000 and sign in as the admin
```

The 32B tier runs identically to the local 7B dev setup: only
`reason.model` / `reason.endpoint` (and the GPU it lands on) change. Switch back
to a local Ollama tier by setting `reason.backend=ollama` and disabling vLLM
(`--set vllm.enabled=false`).
people together at an entrance doorway
## Local validation (run on the dev box, no cluster needed)

```bash
helm lint deploy/helm/sentigon
helm template sentigon deploy/helm/sentigon -f deploy/helm/sentigon/values-runpod.yaml | kubectl apply --dry-run=client -f -
```
