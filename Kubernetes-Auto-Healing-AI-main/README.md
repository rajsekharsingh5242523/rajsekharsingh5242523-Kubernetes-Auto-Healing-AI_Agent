# kube-ai-sre-agent

**AI-assisted Kubernetes SRE agent for memory-related incident detection, diagnosis, and policy-gated remediation**

---

## Why this MVP exists

Modern Kubernetes environments generate vast operational signals (events, logs, metrics), yet incident response remains largely manual and reactive.  
This MVP demonstrates how **AI-assisted reasoning** can be embedded directly into cluster operations to:

- Detect memory-related failures (OOMKilled, restart loops)
- Correlate Kubernetes signals into a single incident context
- Produce **human-readable root-cause explanations**
- Recommend corrective actions
- Enforce **policy-based safety controls** before any remediation

This is **not a self-healing black box**.  
It is a **controlled, explainable, and auditable SRE assistant**.

---

## About the company

This project is built as a **practical engineering MVP** by **ERUTALIA**  
(**https://erutalia.com**)

**ERUTALIA** builds:
- Full-stack platforms
- Knowledge graphs and system intelligence layers
- Private RAG (Retrieval-Augmented Generation) systems for sensitive documents
- Secure AI architectures where **data privacy and explainability are non-negotiable**

This MVP reflects how we design production systems:
- Fail-safe by default
- Human-in-the-loop
- Policy-first
- Explainability over automation hype

---

## Architecture overview
Kubernetes (kind)
|
|-- Pod events / status
|-- Container logs (tail)
|-- Resource specs
v
kube-ai-sre-agent
|
|-- Incident context builder
|-- Policy gate
|-- LLM reasoning (Ollama / Mistral)
v
Human-readable incident report


Key design principles:
- One LLM call per pod (no global hallucinations)
- Structured context + free-text explanation
- No automatic remediation in MVP
- Agent never crashes if LLM fails or times out

---

## Prerequisites

### 1. Docker (required)

Docker must be installed and running.

### 2. Kubernetes via kind

This MVP uses **kind** (Kubernetes in Docker) for fast local testing.

### 3. Ollama with Mistral (local LLM)

Ollama must be running locally with Mistral installed.

### 4. Install Mistral inside Ollama:

```bash
ollama pull mistral
```

### 5. Verify Ollama:
```bash
curl http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"mistral","prompt":"Say hello","stream":false}'
```

## Fresh start (demo environment)

### 1. Check the namespace
```bash
kubectl delete namespace demo
kubectl create namespace demo
```

### 2. Start slow memory pressure pod
```bash
kubectl apply -n demo -f slow-memory-hog.yml
kubectl get pods -n demo -w

```
### 3. Start Hard OOM pod
```bash
kubectl apply -n demo -f memory-hog.yml
kubectl get pods -n demo -w
```

### 4. Inspect pod details:
```bash
kubectl describe pod memory-hog -n demo
```

### 4. Running the agent
```bash
source venv/bin/activate
pip3 install requirements.txt
python3.12 agent.py
```

## Output
```bash
🔄 Scanning cluster state...
⚠️  Detected problematic pod: memory-hog

📌 Incident Report
Pod: memory-hog
Namespace: demo
Node: kube-ai-sre-control-plane
Memory Limit: 64Mi
Root causes: [raw-text-from-LLM]
Confidence: 95%
Recommended Memory: 256Mi
Policy Allowed: True
Auto-remediation: DISABLED (MVP)
```

## What this MVP deliberately does NOT do
- No automatic memory changes
- No pod restarts
- No cluster-wide learning
- No hidden remediation logic

## All actions are:
- Observable
- Explainable
- Policy-controlled

## Optional improvements (roadmap)
- Watch API instead of polling (near real-time detection)
- Event-driven incident ingestion
- Remediation approval workflows
- Memory limit tuning via policies
- Incident history and trend analysis
- Secure RAG over historical incidents
- Multi-signal correlation beyond memory (CPU, IO, network)
