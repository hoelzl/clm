"""Formatting utilities for monitor TUI."""

from datetime import datetime


def format_elapsed(seconds: int) -> str:
    """Format elapsed time in human-readable format.

    Args:
        seconds: Elapsed seconds

    Returns:
        Formatted string (e.g., "02:15", "1:45:30")
    """
    if seconds < 60:
        return f"00:{seconds:02d}"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"


def format_timestamp(dt: datetime, relative: bool = False) -> str:
    """Format timestamp for display.

    Args:
        dt: Datetime object
        relative: If True, show relative time (e.g., "2s ago")

    Returns:
        Formatted timestamp string
    """
    if relative:
        now = datetime.now()
        delta = (now - dt).total_seconds()

        if delta < 60:
            return f"{int(delta)}s ago"
        elif delta < 3600:
            return f"{int(delta // 60)}m ago"
        elif delta < 86400:
            return f"{int(delta // 3600)}h ago"
        else:
            return f"{int(delta // 86400)}d ago"
    else:
        return dt.strftime("%H:%M:%S")


def format_size(bytes_value: int) -> str:
    """Format file size in human-readable format.

    Args:
        bytes_value: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB", "256 KB")
    """
    if bytes_value < 1024:
        return f"{bytes_value} B"
    elif bytes_value < 1024 * 1024:
        return f"{bytes_value / 1024:.1f} KB"
    elif bytes_value < 1024 * 1024 * 1024:
        return f"{bytes_value / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_value / (1024 * 1024 * 1024):.2f} GB"


def format_rate(count: int, period_seconds: int) -> str:
    """Format rate (jobs per minute/second).

    Args:
        count: Number of items
        period_seconds: Time period in seconds

    Returns:
        Formatted rate string
    """
    if period_seconds == 0:
        return "0.0/min"

    rate_per_second = count / period_seconds

    if rate_per_second < 1:
        # Show as per minute if less than 1 per second
        rate_per_minute = count / (period_seconds / 60)
        return f"{rate_per_minute:.1f}/min"
    else:
        return f"{rate_per_second:.1f}/sec"
