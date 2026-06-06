"""Sink implementations — each file implements the Sink Protocol."""

from sinks.counted import CountedSink
from sinks.fanout import FanOutSink
from sinks.json_file import JsonFileSink
from sinks.routing import RoutingSink
from sinks.telegram_posting import TelegramPostingSink

__all__ = ["CountedSink", "FanOutSink", "JsonFileSink", "RoutingSink", "TelegramPostingSink"]
