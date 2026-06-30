from telegram import MessageEntity

from src.instagram_video_bot.services.rich_text import (
    RichText, bold, code, command_reply_rich_text, media_caption_rich_text,
    plain)


def test_rich_text_entities_use_utf16_offsets_for_cyrillic_and_emoji():
    text = RichText.compose(
        plain("Привет "),
        bold("🙂OK"),
        plain(" "),
        code("тест"),
    )

    assert text.text == "Привет 🙂OK тест"
    assert [
        (entity.type, entity.offset, entity.length) for entity in text.entities
    ] == [
        (MessageEntity.BOLD, 7, 4),
        (MessageEntity.CODE, 12, 4),
    ]


def test_command_reply_rich_text_preserves_text_and_marks_commands():
    text = "Команды:\n- /formats - примеры поддерживаемых ссылок\n"

    rich_text = command_reply_rich_text(text)

    assert rich_text.text == text
    assert [
        (entity.type, entity.offset, entity.length) for entity in rich_text.entities
    ] == [
        (MessageEntity.BOLD, 0, len("Команды:")),
        (MessageEntity.CODE, len("Команды:\n- "), len("/formats")),
    ]


def test_command_reply_rich_text_does_not_treat_slash_separated_words_as_commands():
    text = "Supported links:\n- Twitter/X: /status/... links\n"

    rich_text = command_reply_rich_text(text)

    assert [
        (entity.type, entity.offset, entity.length) for entity in rich_text.entities
    ] == [
        (MessageEntity.BOLD, 0, len("Supported links:")),
        (
            MessageEntity.CODE,
            len("Supported links:\n- Twitter/X: "),
            len("/status"),
        ),
    ]


def test_command_reply_rich_text_does_not_overlap_commands_with_bold_rows():
    text = "Commands:\n- /chaos status - chaos mode status: off\n"

    rich_text = command_reply_rich_text(text)

    assert [
        (entity.type, entity.offset, entity.length) for entity in rich_text.entities
    ] == [
        (MessageEntity.BOLD, 0, len("Commands:")),
        (MessageEntity.CODE, len("Commands:\n- "), len("/chaos")),
    ]


def test_media_caption_rich_text_preserves_caption_and_formats_static_prefix():
    rich_text = media_caption_rich_text("🙂 title")

    assert rich_text.text == "Медиа: 🙂 title"
    assert [
        (entity.type, entity.offset, entity.length) for entity in rich_text.entities
    ] == [
        (MessageEntity.BOLD, 0, len("Медиа:")),
    ]
