"""Account manager for handling multiple Instagram accounts."""
import json
import logging
import random
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config.settings import settings

logger = logging.getLogger(__name__)

@dataclass
class Account:
    """Instagram account data."""
    username: str
    password: str = ""
    totp_secret: str = ""
    proxy: Optional[str] = None
    last_used: Optional[datetime] = None
    is_banned: bool = False
    session_file: Optional[Path] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'username': self.username,
            'password': self.password,
            'totp_secret': self.totp_secret,
            'proxy': self.proxy,
            'last_used': self.last_used.isoformat() if self.last_used else None,
            'is_banned': self.is_banned,
            'session_file': str(self.session_file) if self.session_file else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Account':
        """Create from dictionary."""
        return cls(
            username=data['username'],
            password=data.get('password', ''),
            totp_secret=data.get('totp_secret', ''),
            proxy=data.get('proxy'),
            last_used=datetime.fromisoformat(data['last_used']) if data.get('last_used') else None,
            is_banned=data.get('is_banned', False),
            session_file=Path(data['session_file']) if data.get('session_file') else None
        )

class AccountManager:
    """Manages multiple Instagram accounts with rotation and health tracking."""
    
    def __init__(self, accounts_file: Path = Path('accounts.txt'), 
                 state_file: Path = Path('accounts_state.json')):
        """Initialize account manager."""
        self.accounts_file = accounts_file
        self.state_file = state_file
        self.accounts: List[Account] = []
        self.current_account: Optional[Account] = None
        self.sessions_dir = Path('sessions')
        self.sessions_dir.mkdir(exist_ok=True)
        
        # Get available proxies
        self.proxies = settings.get_proxy_list()
        if self.proxies:
            logger.info(f"Loaded {len(self.proxies)} proxies for rotation")
        
        self._load_accounts()
        self._load_state()
    
    def _load_accounts(self) -> None:
        """Load accounts from file."""
        if not self.accounts_file.exists():
            logger.warning(f"No accounts file found: {self.accounts_file}")
            return
        
        logger.info(f"Loading accounts from {self.accounts_file}")
        
        with open(self.accounts_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                try:
                    # Format: username|password|totp_secret
                    parts = line.split('|')
                    if len(parts) >= 3:
                        username = parts[0].strip()
                        password = parts[1].strip()
                        totp_secret = parts[2].strip()
                        
                        # Assign proxy (round-robin through available proxies)
                        proxy = None
                        if self.proxies:
                            proxy_index = (len(self.accounts)) % len(self.proxies)
                            proxy = self.proxies[proxy_index]
                        
                        # Set session file path
                        session_file = self.sessions_dir / f"{username}.json"
                        
                        account = Account(
                            username=username,
                            password=password,
                            totp_secret=totp_secret,
                            proxy=proxy,
                            session_file=session_file
                        )
                        self.accounts.append(account)
                        logger.info(f"Loaded account: {username} with proxy: {proxy or 'None'}")
                    else:
                        logger.warning(f"Invalid format on line {line_num}: Expected username|password|totp_secret")
                        
                except Exception as e:
                    logger.error(f"Error parsing line {line_num}: {e}")
        
        logger.info(f"Loaded {len(self.accounts)} accounts total")
    
    def _load_state(self) -> None:
        """Load account state from JSON file."""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            # Update accounts with saved state
            for saved_account in state.get('accounts', []):
                for account in self.accounts:
                    if account.username == saved_account['username']:
                        account.last_used = datetime.fromisoformat(saved_account['last_used']) if saved_account.get('last_used') else None
                        account.is_banned = saved_account.get('is_banned', False)
                        # Update proxy if changed
                        if saved_account.get('proxy'):
                            account.proxy = saved_account['proxy']
                        if saved_account.get('session_file'):
                            account.session_file = Path(saved_account['session_file'])
                        break
                        
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
    
    def _save_state(self) -> None:
        """Save account state to JSON file."""
        try:
            state = {
                'accounts': [acc.to_dict() for acc in self.accounts],
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def get_available_accounts(self) -> List[Account]:
        """Get list of available (non-banned) accounts."""
        return [
            acc for acc in self.accounts 
            if not acc.is_banned and acc.password and acc.totp_secret
        ]
    
    def get_next_account(self) -> Optional[Account]:
        """Get the next available account for rotation."""
        available = self.get_available_accounts()
        
        if not available:
            logger.error("No available accounts for rotation")
            return None
        
        # Sort by last used time (oldest first)
        available.sort(key=lambda acc: acc.last_used or datetime.min)
        
        # Add some randomness if multiple accounts haven't been used recently
        if len(available) > 1:
            # If top account was used recently, add randomness
            top_account = available[0]
            if top_account.last_used and (datetime.now() - top_account.last_used) < timedelta(hours=1):
                # Choose from top 3 least recently used
                candidates = available[:min(3, len(available))]
                return random.choice(candidates)
        
        return available[0]
    
    def setup_account(self, account: Account) -> bool:
        """Setup an account for use with instagrapi."""
        logger.info(f"Setting up account: {account.username}")
        
        try:
            from ..services.instagram_client import InstagramClient
            
            # Create Instagram client with account's proxy
            client = InstagramClient(
                username=account.username,
                password=account.password,
                session_file=account.session_file,
                proxy=account.proxy
            )
            
            # Attempt login
            if client.login():
                account.last_used = datetime.now()
                self.current_account = account
                self._save_state()
                logger.info(f"Successfully logged in: {account.username} with proxy: {account.proxy or 'None'}")
                return True
            else:
                logger.error(f"Failed to login: {account.username}")
                return False
                    
        except Exception as e:
            logger.error(f"Error setting up account {account.username}: {e}")
            return False
    
    def mark_account_banned(self, account: Account) -> None:
        """Mark an account as banned and try to rotate."""
        logger.warning(f"Marking account as banned: {account.username}")
        account.is_banned = True
        self._save_state()
        
        # Try to rotate to next account
        if self.rotate_account():
            logger.info("Successfully rotated to next account")
        else:
            logger.error("No accounts available after marking as banned")
    
    def rotate_account(self) -> bool:
        """Rotate to the next available account."""
        next_account = self.get_next_account()
        
        if not next_account:
            logger.error("No accounts available for rotation")
            return False
        
        if next_account == self.current_account:
            logger.info("Already using the best available account")
            return True
        
        logger.info(f"Rotating from {self.current_account.username if self.current_account else 'None'} to {next_account.username}")
        return self.setup_account(next_account)
    
    def reset_banned_accounts(self) -> None:
        """Reset banned status for all accounts."""
        reset_count = 0
        for account in self.accounts:
            if account.is_banned:
                account.is_banned = False
                reset_count += 1
        
        self._save_state()
        logger.info(f"Reset {reset_count} banned accounts")
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all accounts."""
        total = len(self.accounts)
        available = len(self.get_available_accounts())
        banned = sum(1 for acc in self.accounts if acc.is_banned)
        
        return {
            'total_accounts': total,
            'available_accounts': available,
            'banned_accounts': banned,
            'current_account': self.current_account.username if self.current_account else None,
            'accounts': [
                {
                    'username': acc.username,
                    'is_banned': acc.is_banned,
                    'proxy': acc.proxy or 'None',
                    'last_used': acc.last_used.isoformat() if acc.last_used else None,
                    'has_session': acc.session_file and acc.session_file.exists() if acc.session_file else False
                }
                for acc in self.accounts
            ]
        }

# Global instance
_account_manager: Optional[AccountManager] = None

def get_account_manager() -> Optional[AccountManager]:
    """Get or create the global account manager instance."""
    global _account_manager
    
    if _account_manager is None:
        accounts_file = Path('accounts.txt')
        
        if accounts_file.exists():
            _account_manager = AccountManager(accounts_file=accounts_file)
            logger.info("Using multi-account mode with instagrapi")
        else:
            logger.info("No accounts file found - using single account mode")
            return None
    
    return _account_manager 