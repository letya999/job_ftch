"""Sink implementations - each file implements the Sink Protocol."""

from job_ftch.sinks.counted import CountedSink
from job_ftch.sinks.failure_tolerant import FailureTolerantSink
from job_ftch.sinks.fanout import FanOutSink
from job_ftch.sinks.json_file import JsonFileSink
from job_ftch.sinks.routing import RoutingSink
from job_ftch.sinks.telegram_posting import TelegramPostingSink

__all__ = [
    "CountedSink",
    "FailureTolerantSink",
    "FanOutSink",
    "JsonFileSink",
    "RoutingSink",
    "TelegramPostingSink",
]
