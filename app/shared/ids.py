from uuid import UUID, uuid4

# 统一生成UUID主键，便于模型通过default引用。
def new_id() -> UUID:
    return uuid4()
