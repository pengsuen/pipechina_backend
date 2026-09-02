"""仅在启动和迁移时导入所有模块模型；业务代码仍由各模块所有。"""

from app.modules.handover.domain.models import *  # noqa: F403
from app.modules.inspection.domain.models import *  # noqa: F403
from app.modules.maintenance_order.domain.models import *  # noqa: F403
from app.modules.operation_event.domain.models import *  # noqa: F403
from app.modules.report.domain.models import *  # noqa: F403
from app.shared.platform.models import *  # noqa: F403
from app.shared.security.authorization.models import *  # noqa: F403
from app.shared.security.identity.models import *  # noqa: F403
