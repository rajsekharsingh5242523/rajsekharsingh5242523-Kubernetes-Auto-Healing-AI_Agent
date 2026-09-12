import json
import time
import urllib.request
from kubernetes import client, config
from kubernetes.client.rest import ApiException


# ============================================================
# CONFIGURATION
# ============================================================

NAMESPACE = "demo"
DEPLOYMENT_NAME = "memory-hog"

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

POLL_INTERVAL_SECONDS = 5

HEALTHY_MEMORY = "256Mi"


# ============================================================
# KUBERNETES CONNECTION
# ============================================================

def init_kubernetes():
    try:
        config.load_kube_config()
        print("☸️ Kubernetes connection established")
    except Exception as e:
        print(f"❌ Could not connect to Kubernetes: {e}")
        raise

    core_api = client.CoreV1Api()
    apps_api = client.AppsV1Api()

    return core_api, apps_api


# ============================================================
# OLLAMA
# ============================================================

def query_ollama(incident_context):

    system_prompt = """
You are a Kubernetes SRE agent.

Return ONLY valid JSON.

Use exactly this structure:
{
  "incident_type": "OOMKilled",
  "root_cause": "short explanation",
  "action": "increase_memory",
  "target": "memory-hog",
  "recommended_memory": "256Mi",
  "confidence": 0.9,
  "reasoning": "short explanation"
}

Rules:
- incident_type MUST be the failure type, such as OOMKilled or CrashLoopBackOff.
- target MUST be the Kubernetes workload name.
- action MUST be exactly increase_memory or no_action.
- confidence MUST be a number from 0 to 1.
- recommended_memory MUST be a Kubernetes value such as 256Mi.
- Return ONLY JSON.
"""
    user_prompt = f"""
Incident information:

{json.dumps(incident_context, indent=2)}
"""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": system_prompt + "\n\n" + user_prompt,
        "stream": False,
        "format": "json"
    }

    try:
        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))

        raw_response = result.get("response", "").strip()

        print("\n🤖 Ollama response received")

        try:
            decision = json.loads(raw_response)
            return decision

        except json.JSONDecodeError:
            print("⚠️ Ollama response was not valid JSON")
            print(raw_response)
            return None

    except Exception as e:
        print(f"❌ Ollama request failed: {e}")
        return None


# ============================================================
# POD INVESTIGATION
# ============================================================

def investigate_pod(core_api, pod):

    pod_name = pod.metadata.name

    print("\n🔎 Investigating incident...")
    print(f"   Pod: {pod_name}")
    print(f"   Namespace: {NAMESPACE}")
    print(f"   Node: {pod.spec.node_name}")
    print(f"   Restarts: {pod.status.container_statuses[0].restart_count if pod.status.container_statuses else 0}")

    container_status = None

    if pod.status.container_statuses:
        container_status = pod.status.container_statuses[0]

    termination_reason = None

    if container_status and container_status.last_state:
        if container_status.last_state.terminated:
            termination_reason = container_status.last_state.terminated.reason

    current_reason = None

    if container_status and container_status.state:
        if container_status.state.waiting:
            current_reason = container_status.state.waiting.reason

    print(f"   Current state: {current_reason}")
    print(f"   Last termination: {termination_reason}")

    # --------------------------------------------------------
    # Logs
    # --------------------------------------------------------

    logs = ""

    try:
        logs = core_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=NAMESPACE,
            tail_lines=50
        )

        print("   📜 Logs collected")

    except Exception:
        print("   ⚠️ Logs unavailable")

    # --------------------------------------------------------
    # Events
    # --------------------------------------------------------

    events = []

    try:
        event_list = core_api.list_namespaced_event(
            namespace=NAMESPACE,
            field_selector=f"involvedObject.name={pod_name}"
        )

        for event in event_list.items:
            events.append({
                "reason": event.reason,
                "message": event.message,
                "type": event.type
            })

        print(f"   📋 Events collected: {len(events)}")

    except Exception:
        print("   ⚠️ Events unavailable")

    # --------------------------------------------------------
    # Memory limit
    # --------------------------------------------------------

    memory_limit = None

    try:
        container = pod.spec.containers[0]

        if container.resources and container.resources.limits:
            memory_limit = container.resources.limits.get("memory")

    except Exception:
        pass

    print(f"   💾 Memory limit: {memory_limit}")

    # --------------------------------------------------------
    # Build incident context
    # --------------------------------------------------------

    incident_type = "CrashLoopBackOff"

    if termination_reason == "OOMKilled":
        incident_type = "OOMKilled"

    incident_context = {
        "incident_type": incident_type,
        "pod": pod_name,
        "namespace": NAMESPACE,
        "deployment": DEPLOYMENT_NAME,
        "node": pod.spec.node_name,
        "restart_count": (
            container_status.restart_count
            if container_status
            else 0
        ),
        "current_reason": current_reason,
        "last_termination_reason": termination_reason,
        "memory_limit": str(memory_limit),
        "events": events[-10:],
        "logs": logs[-50:]
    }

    return incident_context


# ============================================================
# FIND BAD POD
# ============================================================

def find_problematic_pod(core_api):

    try:
        pods = core_api.list_namespaced_pod(NAMESPACE)

    except ApiException as e:
        print(f"❌ Kubernetes API error: {e}")
        return None

    for pod in pods.items:

        if not pod.metadata.labels:
            continue

        if pod.metadata.labels.get("app") != DEPLOYMENT_NAME:
            continue

        if not pod.status.container_statuses:
            continue

        for status in pod.status.container_statuses:

            restart_count = status.restart_count or 0

            waiting_reason = None

            if status.state and status.state.waiting:
                waiting_reason = status.state.waiting.reason

            last_reason = None

            if status.last_state and status.last_state.terminated:
                last_reason = status.last_state.terminated.reason

            if (
                waiting_reason in [
                    "CrashLoopBackOff",
                    "ImagePullBackOff",
                    "ErrImagePull"
                ]
                or last_reason == "OOMKilled"
                or restart_count >= 3
            ):
                return pod

    return None


# ============================================================
# SAFETY POLICY
# ============================================================

def validate_decision(decision):

    if not decision:
        print("❌ No valid AI decision")
        return False

    action = decision.get("action")
    target = decision.get("target")

    confidence = decision.get("confidence", 0)

    try:
        confidence = float(confidence)
    except Exception:
        return False

    # --------------------------------------------------------
    # HARD SAFETY BOUNDARY
    # --------------------------------------------------------

    allowed_actions = [
        "increase_memory",
        "no_action"
    ]

    if action not in allowed_actions:
        print(f"🛑 BLOCKED: action '{action}' is not allowed")
        return False

    if target != DEPLOYMENT_NAME:
        print(f"🛑 BLOCKED: target '{target}' is not allowed")
        return False

    if not (0 <= confidence <= 1):
        print("🛑 BLOCKED: invalid confidence")
        return False

    return True


# ============================================================
# HUMAN APPROVAL
# ============================================================

def request_human_approval(decision):

    print("\n" + "=" * 60)
    print("🧠 AI REMEDIATION PLAN")
    print("=" * 60)

    print(f"Incident type       : {decision.get('incident_type')}")
    print(f"Root cause          : {decision.get('root_cause')}")
    print(f"Target              : {decision.get('target')}")
    print(f"Current memory      : 64Mi")
    print(f"Recommended memory  : {decision.get('recommended_memory')}")
    print(f"Confidence          : {float(decision.get('confidence', 0)) * 100:.0f}%")
    print(f"Reasoning           : {decision.get('reasoning')}")

    print("=" * 60)

    print("\n⏸ Awaiting human approval...")
    answer = input("Approve remediation? [y/N]: ").strip().lower()

    if answer == "y":
        print("✅ Human approval received")
        return True

    print("❌ Remediation rejected")
    return False


# ============================================================
# REMEDIATION
# ============================================================

def remediate(apps_api):

    print("\n🔧 Starting remediation...")
    print("   Action: increase_memory")
    print("   Target: deployment/memory-hog")
    print("   Memory: 64Mi → 256Mi")

    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "hog",
                            "resources": {
                                "requests": {
                                    "memory": "32Mi"
                                },
                                "limits": {
                                    "memory": HEALTHY_MEMORY
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    try:

        apps_api.patch_namespaced_deployment(
            name=DEPLOYMENT_NAME,
            namespace=NAMESPACE,
            body=patch
        )

        print("✅ Deployment remediation submitted")
        return True

    except ApiException as e:

        print(f"❌ Remediation failed: {e}")
        return False


# ============================================================
# VERIFICATION
# ============================================================

def verify_recovery(core_api, apps_api):

    print("\n🔍 Verifying recovery...")

    timeout = 120
    start_time = time.time()

    while time.time() - start_time < timeout:

        try:

            deployment = apps_api.read_namespaced_deployment(
                name=DEPLOYMENT_NAME,
                namespace=NAMESPACE
            )

            desired = deployment.spec.replicas or 0
            available = deployment.status.available_replicas or 0

            pods = core_api.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=f"app={DEPLOYMENT_NAME}"
            )

            running_pod = None

            for pod in pods.items:

                if pod.status.phase == "Running":

                    if pod.status.container_statuses:

                        ready = all(
                            status.ready
                            for status in pod.status.container_statuses
                        )

                        if ready:
                            running_pod = pod
                            break

            print(
                f"   Deployment: {available}/{desired} available"
            )

            if running_pod:

                print(
                    f"   Pod: {running_pod.metadata.name}"
                )

                print("   Status: 1/1 Running")
                print("   Ready: True")

                print("\n" + "=" * 60)
                print("✅ INCIDENT RESOLVED")
                print("=" * 60)

                return True

        except Exception as e:

            print(f"   ⚠️ Verification check failed: {e}")

        time.sleep(3)

    print("\n❌ Verification timed out")
    return False


# ============================================================
# MAIN AGENT LOOP
# ============================================================

def main():

    print("=" * 60)
    print("")
    print("=" * 60)
    print("Mode: Human-approved autonomous remediation")
    print(f"Namespace: {NAMESPACE}")
    print(f"Deployment: {DEPLOYMENT_NAME}")
    print(f"Ollama model: {OLLAMA_MODEL}")
    print("=" * 60)

    core_api, apps_api = init_kubernetes()

    handled_incident = False

    while True:

        print("\n🔄 Scanning cluster state...")

        pod = find_problematic_pod(core_api)

        if pod:

            if handled_incident:
                print("ℹ️ Incident already handled; waiting for recovery.")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            print(
                f"\n⚠️ Detected problematic pod: "
                f"{pod.metadata.name}"
            )

            incident_context = investigate_pod(
                core_api,
                pod
            )

            print("\n🧠 Sending incident to Ollama...")

            decision = query_ollama(
                incident_context
            )

            if not validate_decision(decision):

                print("🛑 AI decision failed safety validation")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            print("\n🎯 AI diagnosis received")

            print(
                f"   Root cause: "
                f"{decision.get('root_cause')}"
            )

            print(
                f"   Recommended action: "
                f"{decision.get('action')}"
            )

            if decision.get("action") == "no_action":

                print("ℹ️ AI recommends no action")
                handled_incident = True
                continue

            approved = request_human_approval(
                decision
            )

            if not approved:

                print(
                    "⏹ Incident remains unresolved."
                )

                handled_incident = True
                continue

            success = remediate(
                apps_api
            )

            if success:

                handled_incident = True

                verify_recovery(
                    core_api,
                    apps_api
                )

        else:

            print("✅ Cluster healthy")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
