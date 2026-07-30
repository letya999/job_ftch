from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("telethon", reason="telethon not installed")

from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources import CareerSiteSource
from job_ftch.infrastructure.sources.career_site_source import (
    _is_filtered_listing_url,
    _is_valid_detail_candidate,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.yandex import YandexJobsParser
from job_ftch.infrastructure.sources.telegram import (
    TelegramChannelSource,
    TelegramCommentSource,
    TelegramGroupSource,
    _client_session,
    _isolated_session_path,
    _safe_iter_messages,
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

    async def get_messages(
        self,
        entity: object,
        *,
        limit: int = 100,
        offset_id: int = 0,
        reply_to: int | None = None,
    ) -> list[FakeMessage]:
        self.calls.append(
            {
                "entity": entity,
                "limit": limit,
                "reply_to": reply_to,
                "offset_id": offset_id,
            }
        )
        items = self._messages if reply_to is None else self._comments_by_post_id.get(reply_to, [])
        if offset_id:
            try:
                idx = next(i for i, msg in enumerate(items) if msg.id == offset_id)
                sliced = items[idx + 1 : idx + 1 + limit]
            except StopIteration:
                sliced = []
        else:
            sliced = items[:limit]
        return sliced


class UnauthorizedTelegramClient(FakeTelegramClient):
    def __init__(self, chat: FakeChat, messages: list[FakeMessage]) -> None:
        super().__init__(chat, messages)
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return False


class HangingTelegramClient(FakeTelegramClient):
    async def get_entity(self, entity: object) -> FakeChat:
        await asyncio.sleep(60)
        return await super().get_entity(entity)


class SessionLockClient(FakeTelegramClient):
    def __init__(
        self,
        chat: FakeChat,
        messages: list[FakeMessage],
        *,
        session_filename: str,
        tracker: dict[str, int],
    ) -> None:
        super().__init__(chat, messages)
        self.session = SimpleNamespace(filename=session_filename)
        self._tracker = tracker

    async def connect(self) -> None:
        self._tracker["active"] += 1
        self._tracker["max_active"] = max(self._tracker["max_active"], self._tracker["active"])
        await asyncio.sleep(0)

    async def disconnect(self) -> None:
        self._tracker["active"] -= 1

    async def is_user_authorized(self) -> bool:
        return True


class FakeResponse:
    def __init__(
        self,
        text: str,
        url: str | None = None,
        status_code: int = 200,
        json_data: Any | None = None,
    ) -> None:
        self.text = text
        self.url = url or "https://example.invalid"
        self.status_code = status_code
        self._json_data = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")
        return None

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


class FakeHttpClient:
    def __init__(self, html: str | None = None, responses: dict[str, str] | None = None) -> None:
        self._html = html
        self._responses = responses or {}

    async def get(
        self, url: str, *, follow_redirects: bool = True, **kwargs: object
    ) -> FakeResponse:
        del kwargs
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


def test_listing_page_and_same_document_fragments_are_not_detail_candidates() -> None:
    board_url = "https://hiremi.ai/"

    assert not _is_valid_detail_candidate(board_url, board_url)
    assert not _is_valid_detail_candidate(f"{board_url}#job-b09jf", board_url)
    assert _is_valid_detail_candidate(
        "https://example.com/vacancies/ml-engineer-123",
        "https://example.com/vacancies?q=ml",
    )


def test_filtered_listing_url_detection() -> None:
    assert _is_filtered_listing_url("https://example.com/vacancies?query=ml")
    assert not _is_filtered_listing_url("https://example.com/vacancies?page=2")


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

    items = await _collect(
        TelegramChannelSource(client, "TelegramTips", limit=10, min_jitter=1.5, max_jitter=1.5)
    )

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.TELEGRAM_CHANNEL
    assert str(items[0].url) == "https://t.me/TelegramTips/209"
    assert items[0].metadata["views"] == 9570000
    assert client.calls == [
        {
            "entity": client._chat,
            "limit": 10,
            "reply_to": None,
            "offset_id": 0,
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
async def test_telegram_channel_source_raises_clear_error_for_unauthorized_session() -> None:
    payload = json.loads(
        _real_world_path("telegram_channel_messages.json").read_text(encoding="utf-8")
    )
    client = UnauthorizedTelegramClient(
        _parse_chat(payload["chat"]),
        [_parse_message(message) for message in payload["messages"]],
    )

    with pytest.raises(RuntimeError, match="Telegram session is not authorized"):
        await _collect(TelegramChannelSource(client, "TelegramTips", limit=10, own_client=True))

    assert client.connected is True
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_telegram_sources_sharing_session_are_serialized() -> None:
    tracker = {"active": 0, "max_active": 0}
    chat = FakeChat(id=1, title="Test", username="test")
    first = SessionLockClient(chat, [], session_filename="shared.session", tracker=tracker)
    second = SessionLockClient(chat, [], session_filename="shared.session", tracker=tracker)

    async def open_session(client: SessionLockClient) -> None:
        async with _client_session(client, own_client=True):
            await asyncio.sleep(0)

    await asyncio.gather(open_session(first), open_session(second))

    assert tracker == {"active": 0, "max_active": 1}


def test_telegram_source_uses_a_separate_copy_of_authorized_session(tmp_path: Path) -> None:
    base = tmp_path / "telegram"
    Path(f"{base}.session").write_bytes(b"authorized-session")

    first = _isolated_session_path(base, source_key="telegram_channel:forproducts")
    second = _isolated_session_path(base, source_key="telegram_channel:ml_jobs_kz")

    assert first != second
    assert Path(f"{first}.session").read_bytes() == b"authorized-session"
    assert Path(f"{second}.session").read_bytes() == b"authorized-session"


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
    assert items[0].metadata["parent_text"] == posts[0].message
    assert items[0].metadata["context_roles"] == ("parent_post", "comment")
    assert client.calls == [
        {
            "entity": client._chat,
            "limit": 5,
            "reply_to": None,
            "offset_id": 0,
        },
        {
            "entity": client._chat,
            "limit": 5,
            "reply_to": 209,
            "offset_id": 0,
        },
    ]


@pytest.mark.asyncio
async def test_telegram_comment_source_skips_invalid_reply_threads() -> None:
    class MsgIdInvalidError(Exception):
        pass

    class ReplyErrorClient(FakeTelegramClient):
        async def get_messages(
            self,
            entity: object,
            *,
            limit: int = 100,
            offset_id: int = 0,
            reply_to: int | None = None,
        ) -> list[FakeMessage]:
            if reply_to == 209:
                raise MsgIdInvalidError(
                    "The message ID used in the peer was invalid (caused by GetRepliesRequest)"
                )
            return await super().get_messages(
                entity,
                limit=limit,
                offset_id=offset_id,
                reply_to=reply_to,
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
async def test_telegram_channel_source_stops_at_freshness_cutoff() -> None:
    client = FakeTelegramClient(
        FakeChat(id=1, title="Fresh", username="fresh"),
        [
            FakeMessage(id=3, message="new", date=datetime(2024, 1, 3, 0, 0, 0, tzinfo=UTC)),
            FakeMessage(id=2, message="still new", date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC)),
            FakeMessage(id=1, message="new", date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)),
        ],
    )

    items = await _collect(
        TelegramChannelSource(
            client,
            "fresh",
            limit=10,
            freshness_cutoff_utc=datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC),
        )
    )

    assert [item.external_id for item in items] == ["3", "2"]


@pytest.mark.asyncio
async def test_telegram_channel_source_times_out_on_hanging_entity_lookup() -> None:
    client = HangingTelegramClient(
        FakeChat(id=1, title="Hanging", username="hanging"),
        [FakeMessage(id=1, message="valid payload", date=datetime(2024, 1, 1, 0, 1, 0))],
    )

    with pytest.raises(TimeoutError):
        await _collect(TelegramChannelSource(client, "hanging", limit=10, timeout_seconds=0.01))


@pytest.mark.asyncio
async def test_safe_iter_messages_retries_once_after_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telethon import errors

    class FloodOnceClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get_messages(
            self,
            entity: object,
            *,
            limit: int = 100,
            offset_id: int = 0,
            reply_to: int | None = None,
        ) -> list[FakeMessage]:
            del entity, limit, offset_id, reply_to
            self.calls += 1
            if self.calls == 1:
                raise errors.FloodWaitError(request=None, capture=1)
            return [FakeMessage(id=1, message="ok", date=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC))]

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("job_ftch.infrastructure.sources.telegram.asyncio.sleep", _no_sleep)

    client = FloodOnceClient()
    messages = [
        msg async for msg in _safe_iter_messages(client, FakeChat(id=1, title="t"), limit=1)
    ]

    assert [msg.id for msg in messages] == [1]
    assert client.calls == 2


@pytest.mark.asyncio
async def test_safe_iter_messages_stops_after_second_consecutive_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telethon import errors

    class FloodTwiceClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get_messages(
            self,
            entity: object,
            *,
            limit: int = 100,
            offset_id: int = 0,
            reply_to: int | None = None,
        ) -> list[FakeMessage]:
            del entity, limit, offset_id, reply_to
            self.calls += 1
            raise errors.FloodWaitError(request=None, capture=1)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("job_ftch.infrastructure.sources.telegram.asyncio.sleep", _no_sleep)

    client = FloodTwiceClient()
    messages = [
        msg async for msg in _safe_iter_messages(client, FakeChat(id=1, title="t"), limit=1)
    ]

    assert messages == []
    assert client.calls == 2


@pytest.mark.asyncio
async def test_greenhouse_board_source_parses_recorded_html_fixture() -> None:
    api_url = "https://boards-api.greenhouse.io/v1/boards/clickhouse/jobs"
    api_payload = {
        "jobs": [
            {
                "absolute_url": "https://job-boards.greenhouse.io/clickhouse/jobs/6014112004",
                "title": "AI Product Engineer - ClickStack",
                "location": {"name": "United States (remote)"},
                "departments": [{"name": "Engineering"}],
            },
            {
                "absolute_url": "https://job-boards.greenhouse.io/clickhouse/jobs/6014113004",
                "title": "AI Research Engineer",
                "location": {"name": "United States (remote)"},
                "departments": [{"name": "Engineering"}],
            },
        ]
    }
    spec = CareerSiteSpec(
        url="https://job-boards.greenhouse.io/clickhouse",
        limit=10,
        source_name="ClickHouse",
    )

    from job_ftch.infrastructure.sources.monitors.greenhouse import discover

    class _ApiResponse:
        def __init__(self, payload: dict[str, Any], url: str) -> None:
            self._payload = payload
            self.url = url
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _ApiClient:
        def __init__(self, responses: dict[str, _ApiResponse]) -> None:
            self._responses = responses

        async def get(
            self,
            url: str,
            *,
            follow_redirects: bool = True,
            **kwargs: object,
        ) -> _ApiResponse:
            del follow_redirects, kwargs
            return self._responses[url]

    items = await discover(
        spec,
        _ApiClient({api_url: _ApiResponse(api_payload, api_url)}),
    )

    assert [item.url for item in items] == [
        "https://job-boards.greenhouse.io/clickhouse/jobs/6014112004",
        "https://job-boards.greenhouse.io/clickhouse/jobs/6014113004",
    ]
    assert items[0].title == "AI Product Engineer - ClickStack"
    assert items[0].locations == ["United States (remote)"]
    assert items[0].metadata["departments"] == ["Engineering"]


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

    spec = CareerSiteSpec(
        url="https://www.bcc.kz/career/vacancies/",
        limit=10,
        source_name="BCC",
        monitor="dom",
        monitor_config={"include_self_url": True},
    )

    items = await _collect(
        CareerSiteSource(
            spec=spec,
            http_client=FakeHttpClient(
                responses={
                    "https://www.bcc.kz/career/vacancies/": list_html,
                    "https://www.bcc.kz/career/28": detail_html,
                }
            ),
            auth=MagicMock(),
        )
    )

    assert len(items) == 1
    assert items[0].source_kind is SourceKind.CAREER_SITE
    assert items[0].source_name == "BCC"
    assert items[0].external_id.endswith("28")
    assert str(items[0].url) == "https://www.bcc.kz/career/28"
    assert "Проектный менеджер" in items[0].text


@pytest.mark.asyncio
async def test_yandex_jobs_parser_prefers_ssr_listing_path() -> None:
    class _FakeClient:
        def __init__(self, responses: dict[str, _FakeResponse]) -> None:
            self._responses = responses

        async def get(self, url: str, *, follow_redirects: bool = True) -> _FakeResponse:
            del follow_redirects
            return self._responses[url]

    class _FakeResponse:
        def __init__(self, text: str, url: str, status_code: int = 200) -> None:
            self.text = text
            self.url = url
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    listing_html = """
    <a href="/jobs/vacancies/data-analyst-123">Vacancy</a>
    <a href="/jobs/vacancies/city_moscow">Filter</a>
    """
    detail_html = """
    <main>
      <h1>Data Analyst</h1>
      <p>Анализировать данные и улучшать ML-продукты.</p>
    </main>
    """
    parser = YandexJobsParser()
    client = _FakeClient(
        {
            "https://yandex.ru/jobs/vacancies?text=ai": _FakeResponse(
                listing_html,
                "https://yandex.ru/jobs/vacancies?text=ai",
            ),
            "https://yandex.ru/jobs/vacancies/data-analyst-123": _FakeResponse(
                detail_html,
                "https://yandex.ru/jobs/vacancies/data-analyst-123",
            ),
        }
    )
    spec = CareerSiteSpec(
        url="https://yandex.ru/jobs/vacancies?text=ai",
        source_name="yandex_jobs_ru",
        limit=1,
    )

    items = [item async for item in parser.parse(spec, client)]

    assert len(items) == 1
    assert items[0].external_id == "123"
    assert "Data Analyst" in items[0].text
