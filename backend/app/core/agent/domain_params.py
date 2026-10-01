"""
域业务参数声明表（路线图 Phase F2 / F2.3）
=============================================
每域一份参数清单，两处消费同一张表：

- **管理端**：``GET /agent-ops/agents/meta`` 用 ``DOMAIN_PARAM_SPECS`` 自动生成表单；
- **运行期**：``resolve()`` 读取定制层覆盖（``agent:{domain}`` 的 ``overrides.params``），
  未覆盖时回退内置默认。

未在此登记的参数，管理端看不见、运行期也不该读——避免"前端能改但后端不生效"
或"后端读了但管理员无从修改"的口径漂移（F2.4 首批只落地报销流免票开关）。
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

# type: bool（开关）/ tri（三态：跟随内置默认 / 强制开 / 强制关）/ number / string
# 表单控件由 type 派生（管理端不另存一份选项表，避免两处口径）
DOMAIN_PARAM_SPECS: dict[str, list[dict[str, Any]]] = {
    "reimbursement": [
        {
            "key": "require_receipt",
            "label": "强制附发票/收据",
            "type": "tri",
            "description": "跟随默认=按报销类别内置规则；关=一律免票；开=一律要票",
        },
    ],
}


def param_specs(domain: str) -> list[dict[str, Any]]:
    return DOMAIN_PARAM_SPECS.get(domain) or []


def resolve(domain: str, key: str, builtin_default: Any) -> Any:
    """
    取域业务参数的生效值：定制覆盖优先，读不到（无库/无覆盖/异常）回退内置默认。

    ``None`` 表示"未覆盖"，因此 tri 型参数的"跟随默认"就是不下发该键。
    """
    try:
        from app.core.agent.customization import customization_service

        override = customization_service.get_domain_param(domain, key, None)
    except Exception as e:  # noqa: BLE001
        logger.warning("domain_params.resolve_failed", domain=domain, key=key, error=str(e))
        return builtin_default
    return builtin_default if override is None else override


def reimb_require_receipt(category_default: bool) -> bool:
    """报销是否必须附发票（F2.4 首批参数；agent 预审与 DSL 预审共用同一判定）。"""
    return bool(resolve("reimbursement", "require_receipt", category_default))
