"""
出站 URL 安全校验（路线图 §2.4 SSRF 防护）
==========================================
管理端可填的外部 MCP URL 属于"服务端替用户发起出站请求"，是 SSRF 的标准入口。
逐条对应 §2.4：

1. 仅允许 http / https；
2. 禁止 IP 直连（IPv4/IPv6 字面量一律拒绝，只允许域名）；
3. 禁止内网/回环/链路本地/组播/保留段，含云元数据地址 169.254.169.254；
4. 域名先过静态规则（``.localhost`` / ``.local`` / ``.internal`` / 元数据主机名），
   再 DNS 解析做二次校验——解析失败按不安全处理（无法证明其公网可达）；
5. 生产叠加出站域名白名单 ``ERP_MCP_OUTBOUND_ALLOWED_DOMAINS``（逗号分隔，含子域）。
   复审备注 3：解析校验存在理论性 DNS rebinding 窗口，白名单才是主控手段，
   故白名单非空时优先判定，解析校验同时保留（纵深防御）。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger()

ALLOWED_SCHEMES = ("http", "https")
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
BLOCKED_HOSTNAMES = ("localhost", "metadata", "metadata.google.internal")


class UnsafeURL(ValueError):
    """URL 不满足出站安全要求（调用方据此返回 400）。"""


# Python 的 is_private 不含 RFC6598 运营商级 NAT 段（100.64.0.0/10），
# 而云内网/覆盖网络（Tailscale、部分 K8s Service 网段）常落在该段 → 显式补上。
_EXTRA_BLOCKED_NETS = (ipaddress.ip_network("100.64.0.0/10"),)


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # 注意：IPv4Address/IPv6Address 没有 is_broadcast（那是 Network 的属性），
    # 255.255.255.255 由 is_reserved/is_private 覆盖。
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return True
    return any(ip in net for net in _EXTRA_BLOCKED_NETS)


def _allowed_by_allowlist(host: str, allowlist: Iterable[str]) -> bool:
    for entry in allowlist:
        e = entry.strip().lower().lstrip(".")
        if not e:
            continue
        if host == e or host.endswith(f".{e}"):
            return True
    return False


def check_url_static(url: str) -> str:
    """不依赖网络的校验：协议 / IP 直连 / 内网字面量 / 保留主机名 / 出站白名单。"""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"仅允许 http/https 出站地址（当前：{scheme or '空'}）")

    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise UnsafeURL("地址缺少主机名")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeURL(f"禁止 IP 直连（仅允许域名，当前：{host}）")

    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeURL(f"内网/保留主机名不允许出站：{host}")

    from app.core.config import settings

    allowlist = [d for d in (settings.mcp_outbound_allowed_domains or "").split(",") if d.strip()]
    if allowlist and not _allowed_by_allowlist(host, allowlist):
        raise UnsafeURL(f"出站域名白名单未包含 {host}（生产环境以白名单为主控手段）")
    return raw


async def resolve_public(host: str) -> list[str]:
    """解析域名并返回公网地址清单；任一解析结果为内网段即判不安全。"""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as e:
        raise UnsafeURL(f"域名无法解析，无法证明其公网可达：{host}（{e}）") from e

    addrs = sorted({info[4][0] for info in infos})
    if not addrs:
        raise UnsafeURL(f"域名解析结果为空：{host}")
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise UnsafeURL(f"解析结果不是合法 IP：{addr}") from None
        if _blocked_ip(ip):
            raise UnsafeURL(f"域名解析到内网/保留地址，已拒绝：{host} → {addr}")
    return addrs


async def validate_outbound_url(url: str) -> str:
    """完整校验（静态 + DNS 二次校验），返回规范化后的 URL。"""
    raw = check_url_static(url)
    host = (urlsplit(raw).hostname or "").strip().rstrip(".").lower()
    await resolve_public(host)
    return raw
