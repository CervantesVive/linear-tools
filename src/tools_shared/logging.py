"""Shared logging utilities"""
import sys

# Global verbose flag
_verbose = False


def setup_logging(verbose: bool = False):
    """Configure logging verbosity"""
    global _verbose
    _verbose = verbose


def log_info(message: str):
    """Log info message (only in verbose mode)"""
    if _verbose:
        print(f"[INFO] {message}", file=sys.stderr)


def log_warning(message: str):
    """Log warning message (always shown)"""
    print(f"[WARNING] {message}", file=sys.stderr)


def log_error(message: str):
    """Log error message (always shown)"""
    print(f"[ERROR] {message}", file=sys.stderr)


def log_success(message: str):
    """Log success message (only in verbose mode)"""
    if _verbose:
        print(f"[SUCCESS] {message}", file=sys.stderr)
