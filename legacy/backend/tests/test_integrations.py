"""
P3-2 外部系统集成适配器模块测试
================================

覆盖范围：
- ``IntegrationRegistry`` 注册 / 获取 / 列表 / 注销 / 连接测试 / 健康检查
- 银行适配器模拟模式（余额 / 转账 / 流水，含审计路径）
- 税务适配器模拟模式（申报 / 状态查询 / 税率表 / 税种校验）
- 签章适配器模拟模式（创建签署 / 状态推进 / 下载 PDF / 错误路径）
- 未配置时的降级行为（占位符凭据 → ``configured: False`` → 模拟模式）
- 异常层次结构
- 真实模式下的异常翻译（认证失败 / 超时 / 网络错误，
  通过 ``httpx.MockTransport`` 模拟，完全离线）
- 模拟模式绝不发起网络请求（替换网络层为报错函数后业务仍正常）

所有测试完全离线运行（不访问任何真实外部系统）。
"""
import httpx
import pytest

from app.core.integrations import (
    BankIntegrationAdapter,
    ESignIntegrationAdapter,
    IntegrationAuthError,
    IntegrationConfigError,
    IntegrationError,
    IntegrationRegistry,
    IntegrationTimeoutError,
    TaxIntegrationAdapter,
    integration_registry,
)

# 集成相关的环境变量（测试中清空以保证"未配置降级"路径的确定性）
_INTEGRATION_ENV_VARS = [
    "ERP_BANK_BASE_URL",
    "ERP_BANK_API_KEY",
    "ERP_BANK_API_SECRET",
    "ERP_TAX_BASE_URL",
    "ERP_TAX_API_KEY",
    "ERP_TAX_ORG_CODE",
    "ERP_ESIGN_BASE_URL",
    "ERP_ESIGN_API_KEY",
    "ERP_ESIGN_APP_ID",
    "ERP_INTEGRATION_TIMEOUT",
]


def _clear_integration_env(monkeypatch) -> None:
    """清空集成相关环境变量（monkeypatch 会在测试后自动恢复）"""
    for var in _INTEGRATION_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ==================================================================
# 异常层次结构
# ==================================================================


def test_exception_hierarchy():
    base = IntegrationError("base")
    auth = IntegrationAuthError("认证失败", provider="bank", detail={"status_code": 401})
    timeout = IntegrationTimeoutError("超时", provider="tax")
    config = IntegrationConfigError("未注册", provider="registry")

    assert isinstance(auth, IntegrationError)
    assert isinstance(timeout, IntegrationError)
    assert isinstance(config, IntegrationError)

    assert auth.provider == "bank"
    assert timeout.provider == "tax"
    assert config.provider == "registry"
    assert auth.detail == {"status_code": 401}
    assert config.detail == {}
    assert "bank" in str(auth)
    assert "detail" in str(auth)


# ==================================================================
# Registry：注册 / 获取 / 列表
# ==================================================================


def test_registry_register_get_list():
    reg = IntegrationRegistry()
    assert reg.list() == []

    bank = BankIntegrationAdapter(base_url="https://bank.test", api_key="sk-placeholder")
    reg.register("bank", bank, config={"env": "test"})

    assert reg.get("bank") is bank

    listing = reg.list()
    assert len(listing) == 1
    entry = listing[0]
    assert entry["name"] == "bank"
    assert entry["provider"] == "bank"
    assert entry["configured"] is False
    assert entry["mock_mode"] is True
    assert entry["config"] == {"env": "test"}

    # 获取未注册的集成 → IntegrationConfigError
    with pytest.raises(IntegrationConfigError):
        reg.get("not-exists")

    # 非法注册参数
    with pytest.raises(IntegrationConfigError):
        reg.register("", bank)
    with pytest.raises(IntegrationConfigError):
        reg.register("bad", object())  # type: ignore[arg-type]

    # 注销（不存在时静默忽略）
    reg.unregister("bank")
    assert reg.list() == []
    reg.unregister("bank")


def test_registry_duplicate_register_overwrites():
    reg = IntegrationRegistry()
    a1 = BankIntegrationAdapter(api_key="sk-placeholder")
    a2 = TaxIntegrationAdapter(api_key="sk-placeholder")
    reg.register("svc", a1)
    reg.register("svc", a2)
    assert reg.get("svc") is a2


# ==================================================================
# 未配置时的降级行为
# ==================================================================


def test_defaults_unconfigured_use_placeholders(monkeypatch):
    """无任何集成环境变量时：内置三件套使用占位符并标记 configured=False"""
    _clear_integration_env(monkeypatch)
    reg = IntegrationRegistry(auto_register=True)

    names = {item["name"] for item in reg.list()}
    assert names == {"bank", "tax", "esign"}

    for name in ("bank", "tax", "esign"):
        adapter = reg.get(name)
        assert adapter.configured is False
        assert adapter.mock_mode is True

    # 全局单例同样降级（重置后按当前环境变量重建）
    integration_registry._reset()
    integration_registry.register_defaults()
    assert integration_registry.get("bank").configured is False
    assert integration_registry.get("bank").mock_mode is True
    assert integration_registry.get("tax").mock_mode is True
    assert integration_registry.get("esign").mock_mode is True


async def test_unconfigured_adapter_still_usable(monkeypatch):
    """未配置的适配器处于模拟模式，业务方法可正常调用（离线降级）"""
    _clear_integration_env(monkeypatch)
    reg = IntegrationRegistry(auto_register=True)

    balance = await reg.get("bank").get_balance("acct-001")
    assert balance["mock"] is True

    sub = await reg.get("tax").submit_tax_return("2026-08", 100.0)
    assert sub["mock"] is True

    req = await reg.get("esign").create_sign_request("doc-001", ["张三"])
    assert req["mock"] is True


# ==================================================================
# 银行适配器 - 模拟模式
# ==================================================================


async def test_bank_mock_mode_balance():
    adapter = BankIntegrationAdapter(
        base_url="https://bank.example.com", api_key="sk-placeholder"
    )
    assert adapter.mock_mode is True

    result = await adapter.get_balance("acct-001")
    assert result["mock"] is True
    assert result["account"] == "acct-001"
    assert result["balance"] == 1000000.0
    assert result["currency"] == "CNY"


async def test_bank_mock_mode_transfer_updates_balance():
    adapter = BankIntegrationAdapter(api_key="sk-placeholder")
    before = await adapter.get_balance("acct-001")

    receipt = await adapter.transfer("acct-001", "acct-002", 1000.0, "供应商付款")
    assert receipt["status"] == "success"
    assert receipt["from_account"] == "acct-001"
    assert receipt["to_account"] == "acct-002"
    assert receipt["amount"] == 1000.0
    assert receipt["transaction_id"].startswith("mock-txn-")
    assert receipt["mock"] is True

    # 付款方余额扣减、收款方余额增加
    after = await adapter.get_balance("acct-001")
    assert after["balance"] == before["balance"] - 1000.0
    to_balance = await adapter.get_balance("acct-002")
    assert to_balance["balance"] == 1000000.0 + 1000.0


async def test_bank_mock_mode_transfer_invalid_amount():
    adapter = BankIntegrationAdapter(api_key="sk-placeholder")
    with pytest.raises(IntegrationError):
        await adapter.transfer("acct-001", "acct-002", 0, "非法金额")
    with pytest.raises(IntegrationError):
        await adapter.transfer("acct-001", "acct-002", -5, "非法金额")


async def test_bank_mock_mode_transactions():
    adapter = BankIntegrationAdapter(api_key="sk-placeholder")

    txs = await adapter.get_transactions("acct-001")
    assert len(txs) >= 2
    assert all(t["account"] == "acct-001" for t in txs)

    # 转账后流水包含该笔记录
    receipt = await adapter.transfer("acct-001", "acct-002", 500.0, "测试转账")
    txs = await adapter.get_transactions("acct-001")
    assert any(t["transaction_id"] == receipt["transaction_id"] for t in txs)

    # 日期范围过滤
    assert await adapter.get_transactions("acct-001", date_from="2099-01-01") == []
    assert await adapter.get_transactions("acct-001", date_from="2026-08-02") != []


# ==================================================================
# 税务适配器 - 模拟模式
# ==================================================================


async def test_tax_mock_mode_submit_and_status():
    adapter = TaxIntegrationAdapter(
        base_url="https://etax.example.gov.cn", api_key="sk-placeholder"
    )
    assert adapter.mock_mode is True

    result = await adapter.submit_tax_return("2026-08", 12500.0, tax_type="vat")
    assert result["status"] == "submitted"
    assert result["submission_id"].startswith("mock-tax-")
    assert result["tax_type"] == "vat"
    assert result["tax_type_name"] == "增值税"
    assert result["mock"] is True

    status = await adapter.query_tax_status(result["submission_id"])
    assert status["submission_id"] == result["submission_id"]
    assert status["status"] == "submitted"

    # 未知提交号
    unknown = await adapter.query_tax_status("does-not-exist")
    assert unknown["status"] == "not_found"


async def test_tax_mock_mode_corporate_income():
    adapter = TaxIntegrationAdapter(api_key="sk-placeholder")
    result = await adapter.submit_tax_return("2026-Q2", 80000.0, tax_type="corporate_income")
    assert result["tax_type_name"] == "企业所得税"
    assert result["mock"] is True


async def test_tax_mock_mode_invalid_tax_type():
    adapter = TaxIntegrationAdapter(api_key="sk-placeholder")
    with pytest.raises(IntegrationConfigError):
        await adapter.submit_tax_return("2026-08", 100.0, tax_type="unknown_tax")
    with pytest.raises(IntegrationConfigError):
        await adapter.get_tax_rates("unknown_tax")


async def test_tax_mock_mode_rates():
    adapter = TaxIntegrationAdapter(api_key="sk-placeholder")

    all_rates = await adapter.get_tax_rates()
    assert all_rates["mock"] is True
    assert "vat" in all_rates["rates"]
    assert "corporate_income" in all_rates["rates"]

    vat = await adapter.get_tax_rates("vat")
    assert vat["tax_type"] == "vat"
    assert any(r["rate"] == 0.13 for r in vat["rates"])


# ==================================================================
# 签章适配器 - 模拟模式
# ==================================================================


async def test_esign_mock_mode_full_flow():
    adapter = ESignIntegrationAdapter(
        base_url="https://esign.example.com", api_key="sk-placeholder"
    )
    assert adapter.mock_mode is True

    req = await adapter.create_sign_request(
        "doc-001", [{"name": "张三", "mobile": "13800000000"}]
    )
    assert req["status"] == "pending"
    assert req["request_id"].startswith("mock-sign-")
    assert req["mock"] is True

    status = await adapter.check_sign_status(req["request_id"])
    assert status["status"] == "completed"
    assert "signed_at" in status
    assert status["signed_file_name"].endswith(".pdf")

    doc = await adapter.download_signed_document(req["request_id"])
    assert isinstance(doc, bytes)
    assert len(doc) > 0


async def test_esign_mock_mode_errors():
    adapter = ESignIntegrationAdapter(api_key="sk-placeholder")

    # 签署人列表为空
    with pytest.raises(IntegrationConfigError):
        await adapter.create_sign_request("doc-001", [])

    # 查询 / 下载不存在的签署请求
    with pytest.raises(IntegrationError):
        await adapter.check_sign_status("missing-request")
    with pytest.raises(IntegrationError):
        await adapter.download_signed_document("missing-request")


# ==================================================================
# Registry：连接测试 / 健康检查
# ==================================================================


async def test_registry_test_connection_mock(monkeypatch):
    _clear_integration_env(monkeypatch)
    reg = IntegrationRegistry(auto_register=True)

    result = await reg.test_connection("bank")
    assert result["status"] == "ok"
    assert result["mode"] == "mock"

    with pytest.raises(IntegrationConfigError):
        await reg.test_connection("missing")


async def test_registry_health_check_mock(monkeypatch):
    _clear_integration_env(monkeypatch)
    reg = IntegrationRegistry(auto_register=True)

    result = await reg.health_check()
    assert result["overall_status"] == "healthy"
    assert result["total"] == 3
    for name in ("bank", "tax", "esign"):
        assert result["connections"][name]["status"] == "ok"
        assert result["connections"][name]["mode"] == "mock"


async def test_registry_health_check_empty():
    reg = IntegrationRegistry()
    result = await reg.health_check()
    assert result["overall_status"] == "no_connections"
    assert result["total"] == 0


async def test_registry_health_check_mixed():
    """部分连接异常时整体状态降级为 degraded（真实模式经 MockTransport 离线模拟）"""

    def _ok_handler(request):
        return httpx.Response(200, json={"status": "ok"})

    def _error_handler(request):
        return httpx.Response(500, json={"error": "boom"})

    reg = IntegrationRegistry()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_ok_handler), base_url="https://bank.test"
    ) as ok_client, httpx.AsyncClient(
        transport=httpx.MockTransport(_error_handler), base_url="https://tax.test"
    ) as err_client:
        reg.register(
            "bank",
            BankIntegrationAdapter(base_url="https://bank.test", api_key="real-key-abc", http_client=ok_client),
        )
        reg.register(
            "tax",
            TaxIntegrationAdapter(base_url="https://tax.test", api_key="real-key-abc", http_client=err_client),
        )
        result = await reg.health_check()
        assert result["overall_status"] == "degraded"
        assert result["connections"]["bank"]["status"] == "ok"
        assert result["connections"]["tax"]["status"] == "error"


# ==================================================================
# 真实模式 - 异常翻译（httpx.MockTransport，完全离线）
# ==================================================================


async def test_real_mode_health_check_ok():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
    async with httpx.AsyncClient(transport=transport, base_url="https://bank.test") as client:
        adapter = BankIntegrationAdapter(
            base_url="https://bank.test", api_key="real-key-abc", http_client=client
        )
        assert adapter.mock_mode is False

        result = await adapter.health_check()
        assert result["status"] == "ok"
        assert result["mode"] == "live"
        assert result["configured"] is True


async def test_real_mode_auth_error_translation():
    def _handler(request):
        return httpx.Response(401, json={"error": "invalid token"})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://bank.test") as client:
        adapter = BankIntegrationAdapter(
            base_url="https://bank.test", api_key="real-key-abc", http_client=client
        )
        with pytest.raises(IntegrationAuthError) as exc_info:
            await adapter.get_balance("acct-001")
        assert exc_info.value.provider == "bank"
        assert exc_info.value.detail["status_code"] == 401


async def test_real_mode_timeout_translation():
    def _handler(request):
        raise httpx.ReadTimeout("simulated read timeout")

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://tax.test") as client:
        adapter = TaxIntegrationAdapter(
            base_url="https://tax.test", api_key="real-key-abc", http_client=client
        )
        with pytest.raises(IntegrationTimeoutError):
            await adapter.get_tax_rates()


async def test_real_mode_network_error_translation():
    def _handler(request):
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://esign.test") as client:
        adapter = ESignIntegrationAdapter(
            base_url="https://esign.test", api_key="real-key-abc", http_client=client
        )
        with pytest.raises(IntegrationError) as exc_info:
            await adapter.create_sign_request("doc-001", ["张三"])
        assert not isinstance(exc_info.value, IntegrationAuthError)
        assert not isinstance(exc_info.value, IntegrationTimeoutError)


async def test_real_mode_transfer_ok_via_mock_transport():
    def _handler(request):
        assert request.url.path == "/api/v1/transfers"
        return httpx.Response(200, json={"transaction_id": "bank-txn-0001", "status": "success"})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://bank.test") as client:
        adapter = BankIntegrationAdapter(
            base_url="https://bank.test", api_key="real-key-abc", http_client=client
        )
        result = await adapter.transfer("acct-001", "acct-002", 100.0, "测试")
        assert result["transaction_id"] == "bank-txn-0001"
        assert result["status"] == "success"


# ==================================================================
# 模拟模式绝不发起网络请求
# ==================================================================


async def test_mock_mode_never_calls_network(monkeypatch):
    """把网络层（_request / _request_bytes）替换为报错函数，
    模拟模式下所有业务方法必须正常返回——证明无任何网络请求。"""

    async def _network_forbidden(*args, **kwargs):
        raise AssertionError("模拟模式不应发起网络请求")

    bank = BankIntegrationAdapter(api_key="sk-placeholder")
    monkeypatch.setattr(bank, "_request", _network_forbidden)
    monkeypatch.setattr(bank, "_request_bytes", _network_forbidden)
    await bank.get_balance("acct-001")
    await bank.transfer("acct-001", "acct-002", 100.0, "r")
    await bank.get_transactions("acct-001")

    tax = TaxIntegrationAdapter(api_key="sk-placeholder")
    monkeypatch.setattr(tax, "_request", _network_forbidden)
    monkeypatch.setattr(tax, "_request_bytes", _network_forbidden)
    sub = await tax.submit_tax_return("2026-08", 100.0)
    await tax.query_tax_status(sub["submission_id"])
    await tax.get_tax_rates()

    esign = ESignIntegrationAdapter(api_key="sk-placeholder")
    monkeypatch.setattr(esign, "_request", _network_forbidden)
    monkeypatch.setattr(esign, "_request_bytes", _network_forbidden)
    req = await esign.create_sign_request("doc-001", ["张三"])
    await esign.check_sign_status(req["request_id"])
    await esign.download_signed_document(req["request_id"])


# ==================================================================
# 全局单例
# ==================================================================


def test_global_singleton_registers_builtin_adapters(monkeypatch):
    _clear_integration_env(monkeypatch)
    integration_registry._reset()
    integration_registry.register_defaults()
    try:
        names = {item["name"] for item in integration_registry.list()}
        assert names == {"bank", "tax", "esign"}
        assert integration_registry.get("bank").mock_mode is True
        assert integration_registry.get("tax").mock_mode is True
        assert integration_registry.get("esign").mock_mode is True
    finally:
        # 恢复全局单例默认状态，避免影响其他测试
        integration_registry._reset()
        integration_registry.register_defaults()
