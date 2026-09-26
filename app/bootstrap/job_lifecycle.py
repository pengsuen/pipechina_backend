from app.modules.knowledge.application.retry import bind_retry
from app.modules.operation_event.application.retry import bind_classification_retry


def retry_handlers():
    """返回任务类型到业务重试绑定器的映射。"""

    return {
        "knowledge_index": bind_retry,
        "knowledge_answer": bind_retry,
        "knowledge_evaluate": bind_retry,
        "event_classification": bind_classification_retry,
    }
