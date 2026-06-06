from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from domain import RawItem, SourceKind
from infrastructure.sources.career_site import CareerSiteSource
from infrastructure.sources.raw_item_factory import build_raw_item
from infrastructure.sources.telegram import (
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
)


@dataclass
class FakeChat:
    id: int
    title: str
    username: str | None = None


@dataclass
class FakeSender:
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    title: str | None = None


@dataclass
class FakeReplyTo:
    reply_to_msg_id: int


@dataclass
class FakeMessage:
    id: int
    message: str
    date: datetime
    sender_id: int | None = None
    sender: FakeSender | None = None
    views: int | None = None
    forwards: int | None = None
    grouped_id: int | None = None
    reply_to: FakeReplyTo | None = None


class FakeTelegramClient:
    def __init__(
        self,
        chat: FakeChat,
        messages: list[FakeMessage],
        comments_by_post_id: dict[int, list[FakeMessage]] | None = None,
    ) -> None:
        self._chat = chat
        self._messages = messages
        self._comments_by_post_id = comments_by_post_id or {}
        self.calls: list[dict[str, object]] = []

    async def get_entity(self, entity: object) -> FakeChat:
        return self._chat

    def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None = None,
        reply_to: int | None = None,
        wait_time: float | None = None,
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "entity": entity,
                "limit": limit,
                "reply_to": reply_to,
                "wait_time": wait_time,
            }
        )
        items = self._messages if reply_to is None else self._comments_by_post_id.get(reply_to, [])
        sliced = items[:limit] if limit is not None else items

        async def _iterate() -> Any:
            for item in sliced:
                yield item

        return _iterate()


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, html: str | None = None, responses: dict[str, str] | None = None) -> None:
        self._html = html
        self._responses = responses or {}

    async def get(self, url: str, *, follow_redirects: bool = True) -> FakeResponse:
        if url in self._responses:
            return FakeResponse(self._responses[url])
        if self._html is None:
            raise KeyError(url)
        return FakeResponse(self._html)


def _real_world_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "real_world" / name


def _parse_chat(payload: dict[str, Any]) -> FakeChat:
    return FakeChat(**payload)


def _parse_message(payload: dict[str, Any]) -> FakeMessage:
    sender_payload = payload.get("sender")
    reply_to_message_id = payload.get("reply_to_message_id")
    return FakeMessage(
        id=payload["id"],
        message=payload["message"],
        date=datetime.fromisoformat(payload["date"]),
        sender_id=payload.get("sender_id"),
        sender=FakeSender(**sender_payload) if sender_payload else None,
        views=payload.get("views"),
        forwards=payload.get("forwards"),
        grouped_id=payload.get("grouped_id"),
        reply_to=FakeReplyTo(reply_to_message_id) if reply_to_message_id else None,
    )


async def _collect(source: object) -> list[RawItem]:
    items: list[RawItem] = []
    async for item in source.fetch():  # type: ignore[attr-defined]
        items.append(item)
    return items


def test_build_raw_item_produces_canonical_shape() -> None:
    item = build_raw_item(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="TelegramTips",
        external_id="209",
        url="https://t.me/TelegramTips/209",
        text="Channel Comments.",
        created_at=datetime(2021, 1, 12, 8, 27, 39),
        metadata={"message_id": 209, "unused": None},
    )

    assert item.source_kind is SourceKind.TELEGRAM_CHANNEL
    assert item.metadata == {"message_id": 209}
    assert item.created_at is not None
    assert item.created_at.utcoffset() is not None


@pytest.mark.asyncio
async def test_telegram_channel_source_maps_real_world_fixture() -> None:
    payload = json.loads(
        _real_world_path("telegram_channel_messages.json").read_text(encoding="utf-8")
    )
    client = FakeTelegramClient(
        _parse_chat(payload["chat"]),
        [_parse_message(message) for message in payload["messages"]],
    )

    items = await _collect(TelegramChannelSource(client, "TelegramTips", limit=10, wait_time=1.5))

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.TELEGRAM_CHANNEL
    assert str(items[0].url) == "https://t.me/TelegramTips/209"
    assert items[0].metadata["views"] == 9570000
    assert client.calls == [
        {
            "entity": client._chat,
            "limit": 10,
            "reply_to": None,
            "wait_time": 1.5,
        }
    ]


@pytest.mark.asyncio
async def test_telegram_channel_source_uses_entity_handle_when_chat_has_no_username() -> None:
    payload = json.loads(
        _real_world_path("telegram_channel_messages.json").read_text(encoding="utf-8")
    )
    chat = _parse_chat(payload["chat"])
    chat.username = None
    client = FakeTelegramClient(chat, [_parse_message(message) for message in payload["messages"]])

    items = await _collect(TelegramChannelSource(client, "@TelegramTips", limit=10))

    assert len(items) == 1
    assert items[0].source_name == "TelegramTips"
    assert str(items[0].url) == "https://t.me/TelegramTips/209"
    assert items[0].metadata["chat_username"] == "TelegramTips"


@pytest.mark.asyncio
async def test_telegram_group_source_maps_sender_metadata() -> None:
    payload = json.loads(
        _real_world_path("telegram_group_messages.json").read_text(encoding="utf-8")
    )
    client = FakeTelegramClient(
        _parse_chat(payload["chat"]),
        [_parse_message(message) for message in payload["messages"]],
    )

    items = await _collect(TelegramGroupSource(client, "brocodersdoubts", limit=10))

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.TELEGRAM_GROUP
    assert items[0].metadata["sender_username"] == "brocoders_admin"
    assert items[0].metadata["sender_display_name"] == "Community Admin"


@pytest.mark.asyncio
async def test_telegram_comment_source_keeps_post_lineage() -> None:
    payload = json.loads(
        _real_world_path("telegram_comment_messages.json").read_text(encoding="utf-8")
    )
    posts = [_parse_message(message) for message in payload["posts"]]
    comments_by_post_id = {
        int(post_id): [_parse_message(message) for message in messages]
        for post_id, messages in payload["comments_by_post_id"].items()
    }
    client = FakeTelegramClient(_parse_chat(payload["chat"]), posts, comments_by_post_id)

    items = await _collect(
        TelegramCommentSource(client, "TelegramTips", post_limit=5, comment_limit_per_post=5)
    )

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.TELEGRAM_COMMENT
    assert items[0].metadata["post_message_id"] == 209
    assert items[0].metadata["post_url"] == "https://t.me/TelegramTips/209"
    assert items[0].metadata["reply_to_message_id"] == 209
    assert client.calls == [
        {
            "entity": client._chat,
            "limit": 5,
            "reply_to": None,
            "wait_time": None,
        },
        {
            "entity": client._chat,
            "limit": 5,
            "reply_to": 209,
            "wait_time": None,
        },
    ]


@pytest.mark.asyncio
async def test_telegram_comment_source_skips_invalid_reply_threads() -> None:
    class MsgIdInvalidError(Exception):
        pass

    class ReplyErrorClient(FakeTelegramClient):
        def iter_messages(  # type: ignore[no-untyped-def]
            self,
            entity: object,
            *,
            limit: int | None = None,
            reply_to: int | None = None,
            wait_time: float | None = None,
        ):
            if reply_to == 209:
                async def _fail() -> Any:
                    raise MsgIdInvalidError(
                        "The message ID used in the peer was invalid (caused by GetRepliesRequest)"
                    )
                    yield  # pragma: no cover

                return _fail()
            return super().iter_messages(
                entity,
                limit=limit,
                reply_to=reply_to,
                wait_time=wait_time,
            )

    payload = json.loads(
        _real_world_path("telegram_comment_messages.json").read_text(encoding="utf-8")
    )
    posts = [_parse_message(message) for message in payload["posts"]]
    extra_post = FakeMessage(
        id=210,
        message="follow-up post",
        date=posts[0].date,
        sender_id=posts[0].sender_id,
        sender=posts[0].sender,
    )
    comments_by_post_id = {
        210: [
            FakeMessage(
                id=310,
                message="Candidate here",
                date=posts[0].date,
                sender_id=1,
                sender=FakeSender(id=1, username="reply_user"),
                reply_to=FakeReplyTo(210),
            )
        ]
    }
    client = ReplyErrorClient(
        _parse_chat(payload["chat"]),
        [posts[0], extra_post],
        comments_by_post_id,
    )

    items = await _collect(
        TelegramCommentSource(client, "TelegramTips", post_limit=5, comment_limit_per_post=5)
    )

    assert len(items) == 1
    assert items[0].external_id == "310"
    assert items[0].metadata["post_message_id"] == 210


@pytest.mark.asyncio
async def test_telegram_sources_skip_blank_or_service_messages() -> None:
    client = FakeTelegramClient(
        FakeChat(id=1, title="Blanky", username="blanky"),
        [
            FakeMessage(id=1, message="  ", date=datetime(2024, 1, 1, 0, 0, 0)),
            FakeMessage(id=2, message="valid payload", date=datetime(2024, 1, 1, 0, 1, 0)),
        ],
    )

    items = await _collect(TelegramChannelSource(client, "blanky", limit=10))

    assert [item.external_id for item in items] == ["2"]


@pytest.mark.asyncio
async def test_greenhouse_board_source_parses_recorded_html_fixture() -> None:
    html = _real_world_path("greenhouse_clickhouse_board.html").read_text(encoding="utf-8")

    items = await _collect(
        CareerSiteSource(
            FakeHttpClient(html),
            "https://job-boards.greenhouse.io/clickhouse",
            limit=10,
        )
    )

    assert [item.external_id for item in items] == ["6014112004", "6014113004"]
    assert items[0].source_kind is SourceKind.CAREER_SITE
    assert items[0].source_name == "ClickHouse"
    assert items[0].metadata["department"] == "Engineering"
    assert items[0].metadata["location"] == "United States (remote)"
    assert items[0].text == "AI Product Engineer - ClickStack\nUnited States (remote)\nEngineering"


@pytest.mark.asyncio
async def test_career_site_source_parses_bcc_list_and_detail_pages() -> None:
    list_html = """
    <div data-ajax-partial="career/list">
      <a href="https://www.bcc.kz/career/28" class="block">
        <div class="rounded-[72px] text-sm">Горящая вакансия</div>
        <div class="text-lg font-bold font-roboto mb-2">Проектный менеджер</div>
        <div class="text-sm mb-2">Алматы</div>
      </a>
    </div>
    """
    detail_html = """
    <div class="bg-white rounded-xl p-6">
      <h1 class="text-lg font-bold font-roboto mb-2">Проектный менеджер</h1>
      <div class="text-sm mb-2">Алматы</div>
      <div class="text-neutral-700">
        <h3>Обязанности</h3>
        <p>Ведение инфраструктурных проектов</p>
        <h3>Требования</h3>
        <p>Опыт управления проектами</p>
      </div>
    </div>
    """

    items = await _collect(
        CareerSiteSource(
            FakeHttpClient(
                responses={
                    "https://www.bcc.kz/career/vacancies/": list_html,
                    "https://www.bcc.kz/career/28": detail_html,
                }
            ),
            "https://www.bcc.kz/career/vacancies/",
            limit=10,
        )
    )

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.CAREER_SITE
    assert items[0].source_name == "BCC"
    assert items[0].external_id == "28"
    assert str(items[0].url) == "https://www.bcc.kz/career/28"
    assert items[0].metadata["location"] == "Алматы"
    assert items[0].metadata["badges"] == ["Горящая вакансия"]
    assert "Обязанности" in items[0].text
