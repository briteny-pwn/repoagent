import hashlib
import hmac
import secrets
import ipaddress
import socket
from urllib.parse import urlsplit
from .integrations import IntegrationError


def password_hash(value, salt):
    return hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check_https(value, allow_query=False):
    try:
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or (p.query and not allow_query) or (p.port and not 1 <= p.port <= 65535):
            raise ValueError()
        try:
            if not ipaddress.ip_address(p.hostname).is_global:
                raise ValueError()
        except ValueError as e:
            if ':' in p.hostname or p.hostname.replace('.', '').isdigit():
                raise ValueError() from e
        if p.hostname.lower() in ('localhost', 'metadata.google.internal'):
            raise ValueError()
        return value.rstrip('/') if not allow_query else value
    except (ValueError, TypeError):
        raise IntegrationError('请输入有效的公网 HTTPS 地址', kind='validation')


def validate_proxy(value, config):
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https', 'socks5', 'socks5h') or not p.hostname or not p.port or p.query or p.fragment or p.path not in ('', '/'):
            raise ValueError()
        allowed = {h.strip() for h in config.allowed_proxy_hosts.split(',') if h.strip()}
        if p.hostname in allowed:
            return value
        # Private/internal proxies are deployment-authorized only.
        addresses = socket.getaddrinfo(p.hostname, p.port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise IntegrationError('代理地址无效；内网代理需在部署端 ALLOWED_PROXY_HOSTS 中允许', kind='validation')


def proxy_for(settings, config):
    network = settings['network']
    if network['mode'] == 'direct':
        return None
    value = network.get('proxy_url')
    if value:
        return validate_proxy(value, config)
    if network['mode'] == 'proxy':
        raise IntegrationError('请配置服务器侧代理地址', kind='validation')
    return None
