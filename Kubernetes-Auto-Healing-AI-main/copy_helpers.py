import json
import requests
import traceback
from kubernetes import client, config


POLICY_FILE   = "policy.json"
MAX_LOG_LINES = 20
OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODEL  = "mistral:latest"
OLLAMA_TIMEOUT= 30  # seconds

# --------------------------------------------------
# Setup
# --------------------------------------------------

def load_policy ():
    with open (POLICY_FILE, "r") as f:
        return json.load (f)

def init_k8s_clients():
    config.load_kube_config ()
    core_v1    = client.CoreV1Api ()
    custom_api = client.CustomObjectsApi ()
    return core_v1, custom_api

# --------------------------------------------------
# Detection
# --------------------------------------------------

def collect_problematic_pods (core_v1):
    pods = core_v1.list_namespaced_pod ("demo").items
    #pods = core_v1.list_pod_for_all_namespaces().items
    print (f"DEBUG: Found {len(pods)} pods")

    problematic = []

    for pod in pods:
        pod_name = pod.metadata.name
        pod_ns   = pod.metadata.namespace

        # 1️⃣ Pod-level failure (restartPolicy: Never)
        if pod.status.phase == "Failed":
            print(f"DEBUG: Failed pod detected {pod_ns}/{pod_name}")
            problematic.append (pod)
            continue

        # 2️⃣ Container-level failures
        for status in pod.status.container_statuses or []:

            # Historical failure (slow-memory-hog)
            if status.last_state and status.last_state.terminated:
                if status.last_state.terminated.reason == "OOMKilled":
                    print(
                        f"DEBUG: OOMKilled (last_state) "
                        f"{pod_ns}/{pod_name}"
                    )
                    problematic.append (pod)
                    break

            # Active failure (memory-hog)
            if status.state and status.state.waiting:
                if status.state.waiting.reason == "CrashLoopBackOff":
                    print (
                        f"DEBUG: CrashLoopBackOff "
                        f"{pod_ns}/{pod_name}"
                    )
                    problematic.append (pod)
                    break

            # Restart-based detection (covers OOMKilled with Always restartPolicy)
            if status.restart_count > 0:
                print (f"DEBUG: Restart detected {pod_ns}/{pod_name} (restarts={status.restart_count})")
                problematic.append (pod)
                break

    return problematic

# --------------------------------------------------
# Signal Collection
# --------------------------------------------------

def collect_pod_events (core_v1, pod):
    events = core_v1.list_namespaced_event (pod.metadata.namespace).items
    return [
        e.message for e in events
        if e.involved_object.name == pod.metadata.name
    ]

def collect_pod_logs (core_v1, pod):
    try:
        logs = core_v1.read_namespaced_pod_log (
            name       = pod.metadata.name,
            namespace  = pod.metadata.namespace,
            tail_lines = MAX_LOG_LINES
        )
        return [
            line for line in logs.splitlines ()
            if "error" in line.lower() or "oom" in line.lower ()
        ]
    except Exception:
        return []

def collect_pod_metrics (custom_api, pod):
    """
    Best-effort metrics collection.
    May return None if metrics are unavailable.
    """
    try:
        metrics = custom_api.get_namespaced_custom_object (
            group    ="metrics.k8s.io",
            version  ="v1beta1",
            namespace=pod.metadata.namespace,
            plural   ="pods",
            name     =pod.metadata.name
        )
        return metrics
    except Exception:
        return None

# --------------------------------------------------
# Correlation
# --------------------------------------------------

def build_incident_context (pod, events, logs, metrics):
    return {
        "pod"         : pod.metadata.name,
        "namespace"   : pod.metadata.namespace,
        "node"        : pod.spec.node_name,
        "events"      : events,
        "logs"        : logs,
        "metrics"     : metrics,
        "memory_limit": pod.spec.containers[0].resources.limits.get ("memory")
    }

# --------------------------------------------------
# LLM Reasoning (Stub)
# --------------------------------------------------

def llm_diagnose (context):
    """
    Placeholder for LLM call (Mistral via Ollama later).
    """
    return {
        "root_cause": "Container exceeded its memory limit and was OOMKilled.",
        "confidence": 0.95,
        "recommended_memory": "256Mi"
    }

# --------------------------------------------------
# Policy Evaluation
# --------------------------------------------------

def evaluate_policy (policy, pod, diagnosis):
    mem_policy = policy["memory_remediation"]

    allowed = pod.metadata.namespace in mem_policy["allowed_namespaces"]

    return {
        "allowed"       : allowed,
        "auto_remediate": mem_policy["auto_remediate"]
    }

# --------------------------------------------------
# Reporting
# --------------------------------------------------

def report_incident (pod, context, diagnosis, decision):
    print ("\n📌 Incident Report")
    print (f"Pod: {pod.metadata.name}")
    print (f"Namespace: {pod.metadata.namespace}")
    print (f"Node: {context['node']}")
    print (f"Memory Limit: {context['memory_limit']}")
    print (f"Diagnosis: {diagnosis['root_cause']}")
    print (f"Confidence: {diagnosis['confidence'] * 100:.0f}%")
    print (f"Recommended Memory: {diagnosis['recommended_memory']}")
    print (f"Policy Allowed: {decision['allowed']}")
    print ("Auto-remediation: DISABLED (MVP)")

# --------------------------------------------------
# Ollama
# --------------------------------------------------

def build_incident_context (pod, container_status):
    """
    Build structured incident data for LLM analysis.
    FUTURE: Add historical incident data here.
    """

    resources = pod.spec.containers[0].resources or {}

    return {
        "incident_type": "OOMKilled"
        if container_status.last_state
        and container_status.last_state.terminated
        and container_status.last_state.terminated.reason == "OOMKilled"
        else "CrashLoopBackOff",

        "pod"           : pod.metadata.name,
        "namespace"     : pod.metadata.namespace,
        "node"          : pod.spec.node_name,
        "container": pod.spec.containers[0].name,
        "restart_policy": pod.spec.restart_policy,
        "restart_count" : container_status.restart_count,
        "pod_status"    : pod.status.phase,

        "resources": {
            "memory_request": (
                resources.requests.get ("memory")
                if resources.requests else None
            ),
            "memory_limit": (
                resources.limits.get ("memory")
                if resources.limits else None
            ),
        },

        "observed_behavior": "Repeated restarts after memory exhaustion",
        "time_window_seconds": 120,

        # FUTURE:
        # "historical_incidents": []
    }

def query_ollama_llm (incident_context):
    """
    Send incident context to Ollama and return raw response text.
    """

    system_prompt = (
        "You are an SRE incident analysis assistant.\n"
        "Analyze Kubernetes incident data provided as JSON.\n"
        "Explain root causes clearly and give practical recommendations.\n"
        "Do NOT invent facts.\n"
        "Do NOT suggest executing commands.\n"
        "Respond in valid JSON followed by an explanation.\n"
        "Confidence must be between 0 and 1."
    )

    user_prompt = (
        "Analyze the following Kubernetes incident:\n\n"
        + json.dumps (incident_context, indent=2)
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "stream": False,
    }

    try:
        response = requests.post (
            OLLAMA_URL,
            json   =payload,
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status ()
        return response.json ().get ("response", "")

    except Exception as e:
        print ("ERROR: Failed to query Ollama")
        traceback.print_exc ()
        return None

def parse_llm_response(raw_text):
    """
    Extract JSON and explanation text from LLM response.
    """

    if not raw_text:
        return None

    try:
        # Attempt to extract JSON block
        start = raw_text.find ("{")
        end   = raw_text.rfind ("}") + 1

        json_part   = raw_text[start:end]
        explanation = raw_text[end:].strip ()

        parsed = json.loads (json_part)
        parsed["explanation_text"] = explanation

        return parsed

    except Exception:
        print ("ERROR: Failed to parse LLM response")
        print (raw_text)
        return None
