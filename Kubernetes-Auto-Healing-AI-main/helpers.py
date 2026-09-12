import json
import requests
import traceback
from kubernetes import client, config


POLICY_FILE   = "policy.json"
MAX_LOG_LINES = 20
OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODEL  = "mistral:latest"
OLLAMA_TIMEOUT= 1200  # seconds

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
    pods = core_v1.list_namespaced_pod (namespace="demo").items
    print (f"DEBUG: Found {len(pods)} pods")

    problematic = []

    for pod in pods:
        pod_name = pod.metadata.name
        pod_ns   = pod.metadata.namespace
        statuses = pod.status.container_statuses or []

        # 1️⃣ Pod-level failure (restartPolicy: Never)     
        if pod.status.phase == "Failed":
            print (f"DEBUG: Failed pod detected {pod_ns}/{pod_name}")
            problematic.append (pod)
            continue

        # 2️⃣ Container-level failures
        for status in statuses:

            # Historical failure (slow-memory-hog)
            if status.last_state and status.last_state.terminated:
                if status.last_state.terminated.reason == "OOMKilled":
                    print(
                        f"DEBUG: OOMKilled (last_state) "
                        f"{pod_ns}/{pod_name}"
                    )
                    problematic.append ((pod, status))
                    break

            # Active failure (memory-hog)
            if status.state and status.state.waiting:
                if status.state.waiting.reason == "CrashLoopBackOff":
                    print (
                        f"DEBUG: CrashLoopBackOff "
                        f"{pod_ns}/{pod_name}"
                    )
                    problematic.append ((pod, status))
                    break

            # Restart-based detection (covers OOMKilled with Always restartPolicy)
            if status.restart_count > 0:
                print (f"DEBUG: Restart detected {pod_ns}/{pod_name} (restarts={status.restart_count})")
                problematic.append ((pod, status))
                break

    return problematic

# --------------------------------------------------
# Signal Collection
# --------------------------------------------------

def collect_pod_events (core_v1, pod, limit=5):
    try:
        events = core_v1.list_namespaced_event(
            namespace=pod.metadata.namespace,
            field_selector=f"involvedObject.name={pod.metadata.name}"
        ).items

        messages = []
        for e in sorted (events, key=lambda x: x.last_timestamp or "", reverse=True):
            if e.reason in ("OOMKilled", "BackOff", "Killing"):
                messages.append (f"{e.reason}: {e.message}")
            if len(messages) >= limit:
                break

        return messages

    except Exception:
        return []

def collect_pod_logs (core_v1, pod, tail_lines=50):
    try:
        log_text = core_v1.read_namespaced_pod_log(
            name      =pod.metadata.name,
            namespace =pod.metadata.namespace,
            tail_lines=tail_lines
        )

        return log_text.splitlines ()[-tail_lines:]

    except Exception:
        return []

def collect_pod_metrics (custom_api, pod):
    try:
        metrics = custom_api.get_namespaced_custom_object (
            group    ="metrics.k8s.io",
            version  ="v1beta1",
            namespace=pod.metadata.namespace,
            plural   ="pods",
            name     =pod.metadata.name
        )

        container = metrics["containers"][0]
        return {
            "memory_usage": container["usage"].get ("memory"),
            "cpu_usage": container["usage"].get ("cpu")
        }

    except Exception:
        return None

# --------------------------------------------------
# Correlation
# --------------------------------------------------

'''def build_incident_context (pod, status, events, logs, metrics):
    return {
        "pod"         : pod.metadata.name,
        "namespace"   : pod.metadata.namespace,
        "node"        : pod.spec.node_name,
        "events"      : events,
        "logs"        : logs,
        "metrics"     : metrics,
        "memory_limit": pod.spec.containers[0].resources.limits.get ("memory"),
        "contextual_signals": {
            "events"      : events,
            "logs_tail"   : logs,
            "metrics"     : metrics
        }
    }'''

# --------------------------------------------------
# LLM Reasoning (Stub)
# --------------------------------------------------

'''def llm_diagnose (context):
    """
    Placeholder for LLM call (Mistral via Ollama later).
    """
    return {
        "root_cause": "Container exceeded its memory limit and was OOMKilled.",
        "confidence": 0.95,
        "recommended_memory": "256Mi"
    }'''

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
    print (f"Node: {context.get('node', 'N/A')}")
    print (f"Memory Limit: {context.get('resources', {}).get('memory_limit', 'N/A')}")

    root_causes = diagnosis.get ("root_causes", []) or {}
    print (f"Diagnosis: {', '.join(root_causes) if root_causes else 'N/A'}")

    print (f"Confidence: {diagnosis.get('confidence', 0) * 100:.0f}%")
    print (f"Recommended Memory: {diagnosis.get('recommended_memory', 'N/A')}")
    print (f"Policy Allowed: {decision.get('allowed', False)}")
    print ("Auto-remediation: DISABLED (MVP)")

# --------------------------------------------------
# Ollama
# --------------------------------------------------

def build_incident_context (pod, container_status, events, logs, metrics):
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
        "contextual_signals": {
            "events"      : events[-5:],
            "logs_tail"   : logs[-10:]
            #"metrics"     : metrics
        }

        # FUTURE:
        # "historical_incidents": []
    }

def query_ollama_llm (incident_context):
    """
    Send incident context to Ollama and return raw response text.
    """

    system_prompt = (
        "You are an SRE incident analysis assistant.\n"
        #"Provide short root cause and memory recommendation."
        "Provide short cause and memory recommendation."
        #"Analyze Kubernetes incident data provided as JSON.\n"
        #"Explain root causes clearly and give practical recommendations.\n"
        #"Do NOT invent facts.\n"
        #"Do NOT suggest executing commands.\n"
        #"Respond in valid JSON followed by an explanation.\n"
        #"Confidence must be between 0 and 1."
    )

    user_prompt = (
        #"Analyze the following Kubernetes incident:\n\n"
        "Look at the Kubernetes incident:\n\n"
        + json.dumps (incident_context, indent=2)
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "stream": False,
    }

    try:
        print ("⏳ Calling Ollama LLM... this may take a while (up to 1200s=20min)")
        print (payload)

        response = requests.post (
            OLLAMA_URL,
            json   =payload,
            timeout=OLLAMA_TIMEOUT,
        )

        response.raise_for_status ()
        return response.json ().get ("response", "")
        #return {}

    except Exception as e:
        print ("ERROR: Failed to query Ollama")
        traceback.print_exc ()
        return None


def parse_llm_response (raw_text):
    """
    Robustly parse LLM response.
    Supports:
        - JSON only
        - JSON + explanation text
        - Plain text fallback
        """

    if not raw_text:
        return {
            "root_causes": [],
            "confidence": 0.0,
            "recommended_memory": "N/A",
            "explanation_text": "Empty LLM response"
        }

    # If Ollama returned dict already (rare but possible)
    if isinstance(raw_text, dict):
        raw_text = raw_text.get("response", "")

    try:
        # Attempt JSON extraction
        start = raw_text.find("{")
        end   = raw_text.rfind("}") + 1

        if start != -1 and end != -1 and end > start:
            json_part = raw_text[start:end]
            parsed = json.loads(json_part)

            explanation = raw_text[end:].strip()
            parsed["explanation_text"] = explanation or parsed.get(
                "explanation_text", ""
            )

            # Normalize expected fields
            parsed.setdefault("root_causes", [])
            parsed.setdefault("confidence", 0.5)
            parsed.setdefault("recommended_memory", "N/A")

            return parsed

    except Exception:
        pass  # fall through to text-only handling

    # 🚑 Fallback: raw text only
    print("⚠️ LLM returned non-JSON text, using fallback parser")

    return {
        "root_causes": [raw_text],
        "confidence": 0.4,
        "recommended_memory": "Consider increasing memory limit",
        "explanation_text": raw_text.strip ()
    }
