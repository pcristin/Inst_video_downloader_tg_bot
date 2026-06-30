"""Small helpers for PTB entity-array rich text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from telegram import MessageEntity

from .chaos_text import ChaosText


def _utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_offset(value: str, char_index: int) -> int:
    return _utf16_len(value[:char_index])


@dataclass(frozen=True)
class RichText:
    """Telegram text plus MessageEntity ranges using UTF-16 offsets."""

    text: str
    entities: list[MessageEntity] = field(default_factory=list)

    @classmethod
    def compose(cls, *parts: "RichText | str") -> "RichText":
        text_parts: list[str] = []
        entities: list[MessageEntity] = []
        offset = 0
        for part in parts:
            rich_part = plain(part) if isinstance(part, str) else part
            text_parts.append(rich_part.text)
            for entity in rich_part.entities:
                entities.append(
                    MessageEntity(
                        type=entity.type,
                        offset=entity.offset + offset,
                        length=entity.length,
                        url=entity.url,
                        user=entity.user,
                        language=entity.language,
                        custom_emoji_id=entity.custom_emoji_id,
                    )
                )
            offset += _utf16_len(rich_part.text)
        return cls("".join(text_parts), entities)


def plain(text: str) -> RichText:
    return RichText(text)


def bold(text: str) -> RichText:
    return RichText(
        text,
        [MessageEntity(type=MessageEntity.BOLD, offset=0, length=_utf16_len(text))],
    )


def code(text: str) -> RichText:
    return RichText(
        text,
        [MessageEntity(type=MessageEntity.CODE, offset=0, length=_utf16_len(text))],
    )


def link(text: str, url: str) -> RichText:
    return RichText(
        text,
        [
            MessageEntity(
                type=MessageEntity.TEXT_LINK,
                offset=0,
                length=_utf16_len(text),
                url=url,
            )
        ],
    )


def command_reply_rich_text(text: str) -> RichText:
    entities: list[MessageEntity] = []
    for match in re.finditer(r"(?m)^([^:\n]{1,48}:)", text):
        entities.append(
            MessageEntity(
                type=MessageEntity.BOLD,
                offset=_utf16_offset(text, match.start(1)),
                length=_utf16_len(match.group(1)),
            )
        )
    for match in re.finditer(r"/[A-Za-z][A-Za-z0-9_]*", text):
        entities.append(
            MessageEntity(
                type=MessageEntity.CODE,
                offset=_utf16_offset(text, match.start()),
                length=_utf16_len(match.group(0)),
            )
        )
    entities.sort(key=lambda entity: entity.offset)
    return RichText(text, entities)


def media_caption_rich_text(title: str) -> RichText:
    caption = ChaosText.media_caption(title)
    prefix = "Медиа:"
    entities = []
    if caption.startswith(prefix):
        entities.append(
            MessageEntity(
                type=MessageEntity.BOLD,
                offset=0,
                length=_utf16_len(prefix),
            )
        )
    return RichText(caption, entities)
