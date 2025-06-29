#!/usr/bin/env python3
"""Monitor Instagram cookie health and send alerts."""
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime

def check_cookies_health() -> bool:
    """Check if cookies are healthy."""
    try:
        result = subprocess.run(
            [sys.executable, 'check_cookies.py'], 
            capture_output=True, 
            text=True,
            timeout=30
        )
        
        # If script exits with 0 and contains success message, cookies are good
        if result.returncode == 0 and "Cookies are working" in result.stdout:
            return True
        else:
            return False
    except Exception as e:
        print(f"Error checking cookies: {e}")
        return False

def send_alert(message: str):
    """Send alert (you can customize this to send to Telegram, email, etc.)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_message = f"[{timestamp}] COOKIE ALERT: {message}"
    
    print(alert_message)
    
    # Write to log file
    log_file = Path('cookie_alerts.log')
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(alert_message + '\n')

def main():
    """Main monitoring loop."""
    print("🔍 Starting Instagram Cookie Monitor")
    print("Press Ctrl+C to stop")
    
    check_interval = 3600  # Check every hour
    consecutive_failures = 0
    max_failures = 3  # Alert after 3 consecutive failures
    
    try:
        while True:
            print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} - Checking cookies...")
            
            if check_cookies_health():
                print("✅ Cookies are healthy")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                print(f"❌ Cookies check failed (attempt {consecutive_failures}/{max_failures})")
                
                if consecutive_failures >= max_failures:
                    send_alert(
                        f"Instagram cookies have failed {consecutive_failures} consecutive checks. "
                        "Bot may not be working. Please refresh cookies."
                    )
                    consecutive_failures = 0  # Reset counter after alert
            
            print(f"💤 Sleeping for {check_interval} seconds...")
            time.sleep(check_interval)
            
    except KeyboardInterrupt:
        print("\n⏹️  Monitoring stopped")
    except Exception as e:
        print(f"\n❌ Monitor error: {e}")

if __name__ == "__main__":
    main() 