"""Sink implementations - each file implements the Sink Protocol."""

from sinks.counted import CountedSink
from sinks.failure_tolerant import FailureTolerantSink
from sinks.fanout import FanOutSink
from sinks.json_file import JsonFileSink
from sinks.routing import RoutingSink
from sinks.telegram_posting import TelegramPostingSink

__all__ = [
    "CountedSink",
    "FailureTolerantSink",
    "FanOutSink",
    "JsonFileSink",
    "RoutingSink",
    "TelegramPostingSink",
]
