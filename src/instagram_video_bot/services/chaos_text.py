"""Russian user-facing text for normal and chaos-mode bot responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextContext:
    """Rendering context for one Telegram-facing message."""

    provider_label: str = ""
    chaos_enabled: bool = False
    private_chat: bool = False


class ChaosText:
    """Build Russian Telegram text without coupling it to bot control flow."""

    @staticmethod
    def help(chaos_enabled: bool) -> str:
        chaos_line = (
            "Режим хаоса включен: буду шумнее реагировать на очереди, повторы и удачные скачивания."
            if chaos_enabled
            else "Режим хаоса выключен. Админ может включить его командой /chaos on."
        )
        return (
            "Пришли ссылку, а я скачаю медиа.\n"
            "Поддерживаю: Instagram, Twitter/X и YouTube Shorts.\n"
            "Команды: /help, /status, /formats, /cancel, /stats, /chaos status\n"
            "Настройки владельца: /quiet on|off, /dupes on|off, /statsmode on|off, "
            "/chatlimit <n>, /userlimit <n>, /admin_status\n"
            f"{chaos_line}"
        )

    @staticmethod
    def formats() -> str:
        return (
            "Поддерживаемые ссылки:\n"
            "- Instagram: посты, reels, stories и share-ссылки\n"
            "- Twitter/X: ссылки вида /status/...\n"
            "- YouTube Shorts: ссылки /shorts/..."
        )

    @staticmethod
    def submission(context: TextContext, *, queue_position: int, joined_existing: bool = False) -> str:
        provider = context.provider_label
        if joined_existing:
            if context.chaos_enabled:
                return f"{provider} уже в работе. Повтор засчитан, сидим рядом с таймером."
            return f"{provider} уже скачивается. Дождусь общего результата."

        if queue_position > 1:
            ahead = queue_position - 1
            if context.chaos_enabled:
                return f"{provider} принят в очередь. Впереди еще {ahead}, в чате уже легкий шум."
            return f"{provider} в очереди. Перед тобой: {ahead}."

        if context.chaos_enabled:
            return f"{provider} принят. Включаю шум, достаю медиа."
        return f"Принял {provider}. Скоро начну скачивать."

    @staticmethod
    def running(context: TextContext) -> str:
        if context.chaos_enabled:
            return f"{context.provider_label}: пошла добыча, не моргаем."
        return f"{context.provider_label}: скачиваю."

    @staticmethod
    def cancelled(chaos_enabled: bool) -> str:
        if chaos_enabled:
            return "Запрос отменен. Драма закончилась раньше скачивания."
        return "Запрос отменен."

    @staticmethod
    def failed(chaos_enabled: bool) -> str:
        if chaos_enabled:
            return "Скачивание упало. Сейчас разберу завалы и скажу, что случилось."
        return "Не удалось скачать медиа."

    @staticmethod
    def unexpected_error() -> str:
        return "Произошла неожиданная ошибка. Попробуй позже."

    @staticmethod
    def error(error: Exception, *, chaos_enabled: bool) -> str:
        error_text = str(error)
        error_lower = error_text.lower()
        if "authentication failed" in error_lower or "cookies have expired" in error_lower:
            return "Не прошла авторизация Instagram. Владельцу бота нужно обновить сессию."
        if "rate-limit" in error_lower or "rate limit" in error_lower:
            if chaos_enabled:
                return "Провайдер включил лимит. Отступаем, чтобы не получить по шапке."
            return "Достигнут лимит провайдера. Попробуй позже."
        if "unsupported" in error_lower:
            return f"Неподдерживаемая ссылка: {error_text}"
        if "timed out" in error_lower:
            return "Скачивание не уложилось по времени. Попробуй еще раз."
        if chaos_enabled:
            return f"Не смог скачать медиа. Причина без прикрас: {error_text}"
        return f"Не смог скачать медиа: {error_text}"

    @staticmethod
    def stats_disabled() -> str:
        return "Статистика выключена для этого чата."

    @staticmethod
    def stats(stats: dict[str, Any], *, chaos_enabled: bool) -> str:
        top_users = ", ".join(f"{name} ({count})" for name, count in stats["top_users"]) or "пока пусто"
        top_providers = ", ".join(
            f"{ChaosText.provider_name(provider)} ({count})" for provider, count in stats["top_providers"]
        ) or "пока пусто"

        if chaos_enabled:
            return (
                "Статистика хаоса:\n"
                f"- Успешных добыч: {stats['completed']}\n"
                f"- Падений: {stats['failed']}\n"
                f"- Отмен: {stats['cancelled']}\n"
                f"- Мгновенных возвратов из кэша: {stats['cache_hits']}\n"
                f"- Повторных ссылок в ту же мясорубку: {stats['duplicate_joins']}\n"
                f"- Главные поставщики ссылок: {top_users}\n"
                f"- Любимые площадки: {top_providers}"
            )

        return (
            "Статистика чата:\n"
            f"- Успешно: {stats['completed']}\n"
            f"- Ошибок: {stats['failed']}\n"
            f"- Отменено: {stats['cancelled']}\n"
            f"- Из кэша: {stats['cache_hits']}\n"
            f"- Повторных ссылок: {stats['duplicate_joins']}\n"
            f"- Топ пользователей: {top_users}\n"
            f"- Топ площадок: {top_providers}"
        )

    @staticmethod
    def status(snapshot: dict[str, int], persisted: dict[str, int], *, chaos_enabled: bool) -> str:
        title = "Статус очереди хаоса" if chaos_enabled else "Статус очереди"
        return (
            f"{title}:\n"
            f"- Активные задачи: {snapshot['active_jobs']}\n"
            f"- В очереди: {snapshot['queued_jobs']}\n"
            f"- Активные запросы: {snapshot['active_requests']}\n"
            f"- Лимит чата: {snapshot['chat_limit']}\n"
            f"- Лимит на пользователя: {snapshot['user_limit']}\n"
            f"- Завершено: {persisted['completed']}\n"
            f"- Ошибок: {persisted['failed']}\n"
            f"- Попаданий в кэш: {persisted['cache_hits']}"
        )

    @staticmethod
    def setting_usage(command_name: str) -> str:
        return f"Использование: /{command_name} on|off"

    @staticmethod
    def setting_updated(label: str, enabled: bool) -> str:
        state = "включено" if enabled else "выключено"
        return f"{label}: {state}."

    @staticmethod
    def numeric_setting_usage(command_name: str) -> str:
        return f"Использование: /{command_name} <положительное число>"

    @staticmethod
    def numeric_setting_updated(label: str, value: int) -> str:
        return f"{label}: {value}."

    @staticmethod
    def owner_unconfigured() -> str:
        return "Команды владельца недоступны: BOT_OWNER_USER_ID не настроен."

    @staticmethod
    def owner_required() -> str:
        return "Эта команда доступна только владельцу бота."

    @staticmethod
    def chaos_usage() -> str:
        return "Использование: /chaos on|off|status"

    @staticmethod
    def chaos_status(enabled: bool) -> str:
        if enabled:
            return "Режим хаоса включен для этого чата."
        return "Режим хаоса выключен для этого чата."

    @staticmethod
    def chaos_updated(enabled: bool) -> str:
        if enabled:
            return "Режим хаоса включен. Теперь бот будет шуметь по делу."
        return "Режим хаоса выключен. Возвращаюсь к спокойному режиму."

    @staticmethod
    def admin_required() -> str:
        return "Режим хаоса могут переключать только админы чата или владелец бота."

    @staticmethod
    def no_active_request() -> str:
        return "У тебя нет активных запросов в очереди или скачивании."

    @staticmethod
    def latest_cancelled() -> str:
        return "Последний запрос отменен."

    @staticmethod
    def too_many_links(limit: int) -> str:
        return f"В очередь попадут только первые {limit} поддерживаемых ссылок."

    @staticmethod
    def media_caption(title: str) -> str:
        return f"Медиа: {title}"

    @staticmethod
    def provider_name(provider: str) -> str:
        return {
            "instagram": "Instagram",
            "twitter": "Twitter/X",
            "youtube_shorts": "YouTube Shorts",
        }.get(provider, provider)
