"""Deployment-only configuration. Application secrets must never have defaults."""
from dataclasses import dataclass, field
import os

@dataclass
class Config:
    data_dir: str = field(default_factory=lambda: os.getenv('DATA_DIR', './data'))
    encryption_key: str = field(default_factory=lambda: os.getenv('ENCRYPTION_KEY', ''))
    admin_password: str = field(default_factory=lambda: os.getenv('ADMIN_PASSWORD', ''))
    secure_cookie: bool = field(default_factory=lambda: os.getenv('SECURE_COOKIE', 'true').lower() == 'true')
    allowed_proxy_hosts: str = field(default_factory=lambda: os.getenv('ALLOWED_PROXY_HOSTS', ''))
    controller_url: str = field(default_factory=lambda: os.getenv('MIHOMO_CONTROLLER_URL', ''))
    controller_secret: str = field(default_factory=lambda: os.getenv('MIHOMO_CONTROLLER_SECRET', ''))
    proxy_config_path: str = field(default_factory=lambda: os.getenv('MIHOMO_CONFIG_PATH', './data/proxy/config.yaml'))
    sidecar_proxy_url: str = field(default_factory=lambda: os.getenv('MIHOMO_PROXY_URL', ''))

    def validate(self):
        if len(self.admin_password) < 16:
            raise ValueError('ADMIN_PASSWORD must contain at least 16 characters')
        if not self.encryption_key:
            raise ValueError('ENCRYPTION_KEY is required')
