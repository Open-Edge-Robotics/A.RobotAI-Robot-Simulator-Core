from datetime import datetime, timezone

from kubernetes import client, config

# config.load_kube_config('/root/.kube/config')
pod_client = client.CoreV1Api()


class PodService:
    @staticmethod
    async def create_pod(instance, template):
        pod_env = client.V1EnvVar(name="AGENT_TYPE", value=template.type)
        pod_label = {"agent-type": template.type}

        pod_name = f"instance-{instance.simulation_id}-{instance.id}"
        pod_metadata = client.V1ObjectMeta(name=pod_name, labels=pod_label)

        container = client.V1Container(
            name=pod_name,
            image="shis1008/pod:latest",
            env=[pod_env],
        )
        pod = client.V1Pod(
            metadata=pod_metadata,
            spec=client.V1PodSpec(containers=[container]),
        )

        pod_namespace = instance.pod_namespace
        pod_client.create_namespaced_pod(namespace=pod_namespace, body=pod)
        return pod_name

    @staticmethod
    async def get_pod_status(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        return pod.status.phase

    @staticmethod
    async def get_pod_image(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        return pod.spec.containers[0].image if pod.spec.containers else ""

    @staticmethod
    async def get_pod_age(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        creation_time = pod.metadata.creation_timestamp
        time_difference = datetime.now(timezone.utc) - creation_time
        age_in_minutes = int(time_difference.total_seconds() // 60)  # 초를 분으로 변환
        return f"{age_in_minutes}min"

    @staticmethod
    async def get_pod_label(pod_name, namespace):
        pod = pod_client.read_namespaced_pod(namespace=namespace, name=pod_name)
        if pod.metadata.labels:
            label = next(iter(pod.metadata.labels.items()))
            return str(label[1])
        return ""

    @staticmethod
    async def delete_pod(pod_name, namespace):
        pod_client.delete_namespaced_pod(name=pod_name, namespace=namespace)
