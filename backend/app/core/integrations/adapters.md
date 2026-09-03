# 外部系统集成适配器（P3-2 企业就绪阶段）

本文档简要说明 `app/core/integrations/` 模块的设计、配置与真实对接方式。

## 模块结构

| 文件            | 职责 |
| --------------- | ---- |
| `base.py`       | `BaseIntegrationAdapter` 基类：凭据管理、模拟模式判定、httpx 客户端与统一异常翻译 |
| `bank.py`       | `BankIntegrationAdapter`：银企直连（余额 / 转账 / 流水） |
| `tax.py`        | `TaxIntegrationAdapter`：电子税务局（申报 / 状态 / 税率表） |
| `esign.py`      | `ESignIntegrationAdapter`：电子签章（创建签署 / 状态 / 下载 PDF） |
| `exceptions.py` | `IntegrationError` 及其子类（Auth / Timeout / Config） |
| `registry.py`   | `IntegrationRegistry` 注册中心 + 全局单例 `integration_registry` |

## 模拟模式（mock mode）

当 API key 为占位符（如 `sk-placeholder`）或未配置时，适配器自动进入模拟模式：

- 不发起任何真实网络请求
- 返回结构完整、带 `"mock": True` 标识的合理模拟数据
- 所有日志事件名以 `integration.` 前缀，便于定位

开发环境与 CI 无需任何外部凭据即可运行全部功能与测试。

## 凭据配置

凭据解析顺序（`registry.py::_resolve_credential`）：

1. `app/core/config.py` 中 `Settings` 字段（如 `bank_base_url`，当前未定义，预留）
2. 环境变量（`ERP_BANK_BASE_URL`、`ERP_BANK_API_KEY` 等）
3. 占位符 + `configured: False`（进入模拟模式）

| 集成 | settings 字段            | 环境变量               | 说明 |
| ---- | ------------------------ | ---------------------- | ---- |
| 银行 | `bank_base_url`          | `ERP_BANK_BASE_URL`    | 银企直连 API 地址 |
| 银行 | `bank_api_key`           | `ERP_BANK_API_KEY`     | 应用令牌 / 证书密钥 |
| 银行 | `bank_api_secret`        | `ERP_BANK_API_SECRET`  | 签名密钥（可选） |
| 税务 | `tax_base_url`           | `ERP_TAX_BASE_URL`     | 电子税务局 API 地址 |
| 税务 | `tax_api_key`            | `ERP_TAX_API_KEY`      | 办税应用令牌 |
| 税务 | `tax_org_code`           | `ERP_TAX_ORG_CODE`     | 纳税人识别号 |
| 签章 | `esign_base_url`         | `ERP_ESIGN_BASE_URL`   | 签署平台 API 地址 |
| 签章 | `esign_api_key`          | `ERP_ESIGN_API_KEY`    | App Secret |
| 签章 | `esign_app_id`           | `ERP_ESIGN_APP_ID`     | App Key / ClientId |
| 通用 | `integration_timeout`    | `ERP_INTEGRATION_TIMEOUT` | 请求超时（秒，默认 10） |

## 使用示例

```python
from app.core.integrations import integration_registry

# 获取全局注册的银行适配器（开发环境为模拟模式）
bank = integration_registry.get("bank")
balance = await bank.get_balance("622200001234")
receipt = await bank.transfer("622200001234", "622200009999", 1000.0, "供应商付款")

# 税务申报（增值税，所属期 2026-08）
tax = integration_registry.get("tax")
sub = await tax.submit_tax_return("2026-08", 12500.0, tax_type="vat")
await tax.query_tax_status(sub["submission_id"])

# 电子签章
esign = integration_registry.get("esign")
req = await esign.create_sign_request("doc-20260807-001", [{"name": "张三", "mobile": "13800000000"}])
await esign.check_sign_status(req["request_id"])
pdf_bytes = await esign.download_signed_document(req["request_id"])

# 连接测试与整体健康检查
await integration_registry.test_connection("bank")
await integration_registry.health_check()
```

## 生产环境真实对接要点

- **银行（银企直连）**：需企业网银证书（U盾 / USBKey）双向 TLS 与报文签名；
  `transfer` 属资金操作，必须前置审批流，以银行回执 `transaction_id` 为准，
  每次转账记录审计日志（`integration.bank.audit_transfer`）。
- **税务（电子税务局）**：需实名办税 + UKey / CA 证书，按税局接口规范组装报文；
  申报后轮询 `query_tax_status` 直至终态（入库 / 扣款结果）。
- **电子签章（e签宝 / 法大大）**：按开放平台 AppKey + AppSecret 签名鉴权；
  签署完成后下载 PDF 归档，配合回调（webhook）验签。
- 各适配器方法的 docstring 中均有对应生产接口约定（路径、请求体、响应）。
