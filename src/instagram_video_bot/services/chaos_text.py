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
            "- /chaos status - статус режима хаоса: включен"
            if chaos_enabled
            else "- /chaos status - статус режима хаоса: выключен"
        )
        return (
            "Пришли ссылку - я скачаю медиа в этот чат.\n\n"
            "Поддерживаю:\n"
            "- Instagram posts, reels, stories\n"
            "- Twitter/X status links\n"
            "- YouTube Shorts\n\n"
            "Команды:\n"
            "- /formats - примеры поддерживаемых ссылок\n"
            "- /status - очередь и состояние бота\n"
            "- /cancel - отменить твой последний запрос\n"
            "- /stats - статистика этого чата\n"
            f"{chaos_line}"
        )

    @staticmethod
    def admin_help() -> str:
        return (
            "Admin commands:\n"
            "/help - public usage help\n"
            "/formats - supported link formats\n"
            "/status - queue status for this chat\n"
            "/cancel - cancel your latest active request\n"
            "/stats - chat stats\n"
            "/chaos on|off|status - manage chat chaos mode\n"
            "/quiet on|off - toggle quiet mode for this chat\n"
            "/dupes on|off - toggle duplicate suppression for this chat\n"
            "/statsmode on|off - toggle stats visibility for this chat\n"
            "/chatlimit <positive number> - set this chat concurrency limit\n"
            "/userlimit <positive number> - set per-user active request limit\n"
            "/admin_status - owner operational status for this chat\n"
            "/admin_global_status - owner operational status across all chats\n"
            "/inline_whitelist add <user_id> | remove <user_id> | list - manage free inline users\n"
            "/inline_price subscription <stars> - set inline monthly subscription price\n"
            "/inline_onetime on <stars> | off - manage one-time inline payments\n"
            "/inline_refund <telegram_payment_charge_id> [user_id] - refund an inline Stars payment\n"
            "Rate limits: USER_RATE_LIMIT_REQUESTS per USER_RATE_LIMIT_WINDOW_SECONDS.\n"
            "Promo: first 3 successful inline deliveries are free per user lifetime.\n"
            "Refunds: 30% subscription refund protection is evaluated after subscription expiry.\n"
            "/admin_help - show this admin command list"
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
            return "Эта ссылка не поддерживается."
        if "timed out" in error_lower:
            return "Скачивание не уложилось по времени. Попробуй еще раз."
        return "Не смог скачать медиа. Попробуй позже."

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

    @staticmethod
    def inline_preparing(provider_label: str) -> str:
        return f"Preparing {provider_label} media..."

    @staticmethod
    def inline_storage_missing() -> str:
        return "Inline delivery is not configured. Set INLINE_STORAGE_CHAT_ID."

    @staticmethod
    def inline_payment_unavailable() -> str:
        return "Inline payments are temporarily unavailable. Try again later."

    @staticmethod
    def inline_delivery_failed() -> str:
        return "Inline delivery failed. If this was a one-time payment, it was refunded."

    @staticmethod
    def rate_limited(retry_after_seconds: int) -> str:
        minutes = max(1, (retry_after_seconds + 59) // 60)
        return f"Слишком много запросов. Попробуй снова примерно через {minutes} мин."

    @staticmethod
    def inline_whitelist_usage() -> str:
        return "Usage: /inline_whitelist add <user_id> | remove <user_id> | list"

    @staticmethod
    def inline_whitelist_added(user_id: int) -> str:
        return f"Inline whitelist: added user {user_id}."

    @staticmethod
    def inline_whitelist_removed(user_id: int) -> str:
        return f"Inline whitelist: removed user {user_id}."

    @staticmethod
    def inline_whitelist_forward_added(user_id: int) -> str:
        return f"Inline whitelist: added forwarded user {user_id}."

    @staticmethod
    def inline_whitelist_list(users: list[dict[str, Any]]) -> str:
        if not users:
            return "Inline whitelist is empty."
        lines = [f"- {user['user_id']}" for user in users]
        return "Inline whitelist:\n" + "\n".join(lines)

    @staticmethod
    def inline_price_usage() -> str:
        return "Usage: /inline_price subscription <stars>"

    @staticmethod
    def inline_subscription_price_updated(stars: int) -> str:
        return f"Inline subscription price: {stars} Stars."

    @staticmethod
    def inline_onetime_usage() -> str:
        return "Usage: /inline_onetime on <stars> | off"

    @staticmethod
    def inline_onetime_updated(runtime: dict[str, Any]) -> str:
        if runtime["one_time_enabled"]:
            return f"Inline one-time payment: on, {runtime['one_time_stars']} Stars."
        return "Inline one-time payment: off."

    @staticmethod
    def inline_refund_usage() -> str:
        return "Usage: /inline_refund <telegram_payment_charge_id> [user_id]"

    @staticmethod
    def inline_refund_sent(user_id: int) -> str:
        return f"Inline refund sent for user {user_id}."

    @staticmethod
    def inline_refund_not_found() -> str:
        return "Inline refund charge not found. Add user_id to refund an unknown charge."

    @staticmethod
    def inline_refund_already_refunded() -> str:
        return "Inline refund was already recorded for this charge."

    @staticmethod
    def inline_refund_failed() -> str:
        return "Inline refund failed. Check logs and try again."
