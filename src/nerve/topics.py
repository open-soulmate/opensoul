"""Event topic constants for OpenNerve messaging."""


class Topics:
    """NATS topic patterns used across the system."""

    TOPIC_HEARTBEAT = "soma.{node_id}.heartbeat"
    TOPIC_DATA_REPORT = "soma.{node_id}.data"
    TOPIC_TASK_ASSIGN = "soul.{node_id}.task"
    TOPIC_KNOWLEDGE_UPDATE = "soul.knowledge.update"
    TOPIC_SYSTEM_EVENT = "system.event"

    @staticmethod
    def heartbeat(node_id: str) -> str:
        return Topics.TOPIC_HEARTBEAT.format(node_id=node_id)

    @staticmethod
    def data_report(node_id: str) -> str:
        return Topics.TOPIC_DATA_REPORT.format(node_id=node_id)

    @staticmethod
    def task_assign(node_id: str) -> str:
        return Topics.TOPIC_TASK_ASSIGN.format(node_id=node_id)
