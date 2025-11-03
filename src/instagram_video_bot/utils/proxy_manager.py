"""Proxy management and rotation utilities."""
import hashlib
import os
import random
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class ProxyConfig:
    """Proxy configuration data."""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    
    @property
    def url(self) -> str:
        """Get proxy URL."""
        if self.username and self.password:
            return f"http://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"http://{self.host}:{self.port}"
    
class ProxyManager:
    """Manages multiple proxies and assigns them to accounts."""
    
    def __init__(self):
        """Initialize proxy manager."""
        self.proxies: List[ProxyConfig] = []
        self._load_proxies()
    
    def _load_proxies(self):
        """Load proxies from environment variables."""
        # Try single proxy format first (legacy)
        proxy_host = os.getenv('PROXY_HOST')
        proxy_port = os.getenv('PROXY_PORT')
        
        if proxy_host and proxy_port:
            proxy = ProxyConfig(
                host=proxy_host,
                port=int(proxy_port),
                username=os.getenv('PROXY_USERNAME'),
                password=os.getenv('PROXY_PASSWORD')
            )
            self.proxies.append(proxy)
            logger.info(f"Loaded single proxy: {proxy.host}:{proxy.port}")
        
        # Try multiple proxy format
        proxy_list = os.getenv('PROXY_LIST')
        if proxy_list:
            for line in proxy_list.strip().split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                try:
                    proxy = self._parse_proxy_line(line)
                    if proxy:
                        self.proxies.append(proxy)
                except Exception as e:
                    logger.warning(f"Failed to parse proxy line '{line}': {e}")
        
        # Try numbered proxy format (PROXY_1, PROXY_2, etc.)
        for i in range(1, 21):  # Support up to 20 proxies
            proxy_env = os.getenv(f'PROXY_{i}')
            if proxy_env:
                try:
                    proxy = self._parse_proxy_line(proxy_env)
                    if proxy:
                        self.proxies.append(proxy)
                except Exception as e:
                    logger.warning(f"Failed to parse PROXY_{i} '{proxy_env}': {e}")
        
        if not self.proxies:
            logger.warning("No proxies loaded! Running without proxy.")
        else:
            logger.info(f"Loaded {len(self.proxies)} proxies")
    
    def _parse_proxy_line(self, line: str) -> Optional[ProxyConfig]:
        """Parse a proxy line in various formats."""
        line = line.strip()
        
        # Format: login:password@ip:port (preferred)
        if '@' in line and line.count(':') >= 2:
            auth_part, host_port = line.split('@', 1)
            if ':' in auth_part and ':' in host_port:
                username, password = auth_part.split(':', 1)
                host, port = host_port.split(':', 1)
                return ProxyConfig(host, int(port), username, password)
        
        # Format: host:port:username:password (legacy)
        elif line.count(':') == 3:
            host, port, username, password = line.split(':')
            return ProxyConfig(host, int(port), username, password)
        
        # Format: host:port (no auth)
        elif line.count(':') == 1:
            host, port = line.split(':')
            return ProxyConfig(host, int(port))
        
        return None
    
    def get_proxy_for_account(self, account_name: str) -> Optional[ProxyConfig]:
        """Get a consistent proxy for a specific account."""
        if not self.proxies:
            return None
        
        # Use hash of account name to consistently assign same proxy
        account_hash = hashlib.md5(account_name.encode()).hexdigest()
        proxy_index = int(account_hash[:8], 16) % len(self.proxies)
        
        proxy = self.proxies[proxy_index]
        logger.info(f"Account {account_name} assigned to proxy {proxy.host}:{proxy.port}")
        return proxy
    
    def get_random_proxy(self) -> Optional[ProxyConfig]:
        """Get a random proxy from the pool."""
        if not self.proxies:
            return None
        return random.choice(self.proxies)
    
    def get_all_proxies(self) -> List[ProxyConfig]:
        """Get all available proxies."""
        return self.proxies.copy()
    
    def test_proxy(self, proxy: ProxyConfig) -> bool:
        """Test if a proxy is working."""
        import requests
        
        try:
            response = requests.get(
                'https://api.ipify.org',
                proxies={'http': proxy.url, 'https': proxy.url},
                timeout=10
            )
            if response.status_code == 200:
                logger.info(f"Proxy {proxy.host}:{proxy.port} is working - IP: {response.text}")
                return True
        except Exception as e:
            logger.warning(f"Proxy {proxy.host}:{proxy.port} failed: {e}")
        
        return False

# Global proxy manager instance
proxy_manager = ProxyManager()

def get_proxy_for_account(account_name: str) -> Optional[ProxyConfig]:
    """Get proxy configuration for a specific account."""
    return proxy_manager.get_proxy_for_account(account_name)

def get_random_proxy() -> Optional[ProxyConfig]:
    """Get a random proxy from the pool."""
    return proxy_manager.get_random_proxy()

def test_all_proxies() -> List[Tuple[ProxyConfig, bool]]:
    """Test all proxies and return results."""
    results = []
    for proxy in proxy_manager.get_all_proxies():
        is_working = proxy_manager.test_proxy(proxy)
        results.append((proxy, is_working))
    return results 
