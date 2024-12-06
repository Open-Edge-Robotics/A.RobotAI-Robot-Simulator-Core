from datetime import datetime, timezone

import yaml
from kubernetes import client, config

config.load_kube_config('/root/.kube/config')
pod_client = client.CoreV1Api()


class PodService:
    @staticmethod
    async def create_pod(instance, template):
        with open("/robot-simulator/src/pod-template.yaml", "r") as f:
            pod = yaml.safe_load(f)

        pod_name = f"instance-{instance.simulation_id}-{instance.id}"
        pod_label = {"agent-type": template.type.lower()}

        pod["metadata"]["name"] = pod_name
        pod["metadata"]["labels"] = pod_label
        pod["spec"]["containers"][0]["name"] = pod_name

        pod_namespace = instance.pod_namespace
        pod_client.create_namespaced_pod(namespace=pod_namespace, body=pod)
        return pod_name

    @staticmethod
    async def create_pod_temp(instance, template):
        pod_name = f"instance-{instance.simulation_id}-{instance.id}"
        pod_label = {"agent-type": template.type.lower()}
        pod_metadata = client.V1ObjectMeta(name=pod_name, labels=pod_label)

        pod_client.connect_get_namespaced_pod_exec()

        pod_env = client.V1EnvVar(name="BAG_FILE_PATH", value=template.bag_file_path)
        container = client.V1Container(
            name=pod_name,
            image="innoagent/pod:1.0",
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

        total_seconds = int(time_difference.total_seconds())
        days, remainder = divmod(total_seconds, 86400)  # 1일 = 86400초
        hours, remainder = divmod(remainder, 3600)  # 1시간 = 3600초
        minutes, seconds = divmod(remainder, 60)  # 1분 = 60초

        time_units = [("d", days), ("h", hours), ("m", minutes), ("s", seconds)]
        return next(f"{value}{unit}" for unit, value in time_units if value > 0)

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

    @staticmethod
    async def create_namespace(simulation_id: int):
        name = f"simulation-{simulation_id}"
        metadata = client.V1ObjectMeta(name=name)
        namespace = client.V1Namespace(metadata=metadata)
        pod_client.create_namespace(namespace)
        return name

    @staticmethod
    async def delete_namespace(simulation_id: int):
        pod_client.delete_namespace(name=f"simulation-{simulation_id}")
