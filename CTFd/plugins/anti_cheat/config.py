# Anti-Cheat Plugin Production Configuration

import os

class ProductionConfig:
    """Production-ready configuration for the anti-cheat plugin"""
    
    # Logging Configuration
    ENABLE_DEBUG_LOGS = os.environ.get('ANTICHEAT_DEBUG', 'False').lower() == 'true'
    LOG_LEVEL = os.environ.get('ANTICHEAT_LOG_LEVEL', 'INFO')
    
    # Performance Settings
    BATCH_SIZE = int(os.environ.get('ANTICHEAT_BATCH_SIZE', '1000'))
    MAX_ALERTS_PER_SCAN = int(os.environ.get('ANTICHEAT_MAX_ALERTS', '100'))
    
    # Cache Settings
    CACHE_TIMEOUT = int(os.environ.get('ANTICHEAT_CACHE_TIMEOUT', '300'))  # 5 minutes
    
    # Alert Thresholds (can be overridden by database config)
    DEFAULT_DUPLICATE_FLAG_THRESHOLD = 1
    DEFAULT_BRUTE_FORCE_THRESHOLD = 10
    DEFAULT_BRUTE_FORCE_WINDOW = 60
    DEFAULT_IP_SHARING_THRESHOLD = 3
    DEFAULT_SEQUENCE_SIMILARITY_THRESHOLD = 0.8
    DEFAULT_TIME_DELTA_THRESHOLD = 30
    
    # Security Settings
    REQUIRE_CSRF_TOKEN = True
    MAX_EVIDENCE_SIZE = 1024 * 1024  # 1MB max evidence data
    
    @staticmethod
    def log_debug(message):
        """Log debug message if debug logging is enabled"""
        if ProductionConfig.ENABLE_DEBUG_LOGS:
            print(f"[DEBUG] {message}")
    
    @staticmethod
    def log_info(message):
        """Log info message"""
        print(f"[INFO] {message}")
    
    @staticmethod
    def log_error(message):
        """Log error message"""
        print(f"[ERROR] {message}")
