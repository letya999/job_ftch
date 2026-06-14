"""Pagination keyboards for the Telegram bot."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class DigestPage(CallbackData, prefix="dp"):
    """Callback data for digest pagination."""

    digest_hash: str
    page: int


class SearchPage(CallbackData, prefix="sp"):
    """Callback data for search pagination."""

    query_hash: str
    page: int


def build_digest_kb(digest_hash: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build keyboard for digest pagination."""
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.add(
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=DigestPage(digest_hash=digest_hash, page=page - 1).pack(),
            )
        )
    if page < total_pages - 1:
        builder.add(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=DigestPage(digest_hash=digest_hash, page=page + 1).pack(),
            )
        )
    return builder.as_markup()


def build_search_kb(query_hash: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build keyboard for search pagination."""
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.add(
            InlineKeyboardButton(
                text="⬅️ Previous",
                callback_data=SearchPage(query_hash=query_hash, page=page - 1).pack(),
            )
        )
    if page < total_pages - 1:
        builder.add(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=SearchPage(query_hash=query_hash, page=page + 1).pack(),
            )
        )
    return builder.as_markup()
