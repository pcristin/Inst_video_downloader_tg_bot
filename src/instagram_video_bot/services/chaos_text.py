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
    language_code: str = "ru"


class ChaosText:
    """Build Russian Telegram text without coupling it to bot control flow."""

    @staticmethod
    def start(language_code: str = "ru") -> str:
        if language_code == "en":
            return (
                "Hi! Send me a link and I will download the media here.\n\n"
                "I support Instagram posts, reels, stories, Twitter/X posts, and YouTube Shorts.\n\n"
                "Useful commands:\n"
                "- /help - usage help\n"
                "- /formats - supported link examples\n"
                "- /status - queue status\n"
                "- /language en|ru - switch language"
            )
        return (
            "Привет! Пришли ссылку - я скачаю медиа сюда.\n\n"
            "Поддерживаю Instagram posts, reels, stories, Twitter/X posts и YouTube Shorts.\n\n"
            "Полезные команды:\n"
            "- /help - помощь\n"
            "- /formats - примеры ссылок\n"
            "- /status - статус очереди\n"
            "- /language en|ru - сменить язык"
        )

    @staticmethod
    def language_usage(language_code: str = "ru") -> str:
        if language_code == "en":
            return "Usage: /language en|ru"
        return "Использование: /language en|ru"

    @staticmethod
    def language_updated(language_code: str) -> str:
        if language_code == "en":
            return "Language set to English."
        return "Язык переключен на русский."

    @staticmethod
    def help(chaos_enabled: bool, language_code: str = "ru") -> str:
        if language_code == "en":
            chaos_line = (
                "- /chaos status - chaos mode status: on"
                if chaos_enabled
                else "- /chaos status - chaos mode status: off"
            )
            return (
                "Send me a link - I will download the media to this chat.\n\n"
                "Supported:\n"
                "- Instagram posts, reels, stories\n"
                "- Twitter/X status links\n"
                "- YouTube Shorts\n\n"
                "Commands:\n"
                "- /formats - supported link examples\n"
                "- /status - bot queue and health\n"
                "- /cancel - cancel your latest request\n"
                "- /stats - stats for this chat\n"
                "- /language en|ru - switch language\n"
                f"{chaos_line}"
            )
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
            "- /language en|ru - сменить язык\n"
            f"{chaos_line}"
        )

    @staticmethod
    def bot_migration_redirect(target_username: str) -> str:
        username = target_username.strip().removeprefix("@")
        return (
            f"Мы переехали в @{username}.\nОткрыть нового бота: https://t.me/{username}"
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
    def formats(language_code: str = "ru") -> str:
        if language_code == "en":
            return (
                "Supported links:\n"
                "- Instagram: posts, reels, stories, and share links\n"
                "- Twitter/X: /status/... links\n"
                "- YouTube Shorts: /shorts/... links"
            )
        return (
            "Поддерживаемые ссылки:\n"
            "- Instagram: посты, reels, stories и share-ссылки\n"
            "- Twitter/X: ссылки вида /status/...\n"
            "- YouTube Shorts: ссылки /shorts/..."
        )

    @staticmethod
    def submission(
        context: TextContext, *, queue_position: int, joined_existing: bool = False
    ) -> str:
        provider = context.provider_label
        if context.language_code == "en":
            if joined_existing:
                if context.chaos_enabled:
                    return f"{provider} is already in progress. Joined the same run."
                return f"{provider} is already downloading. I will wait for the shared result."

            if queue_position > 1:
                ahead = queue_position - 1
                if context.chaos_enabled:
                    return f"{provider} joined the queue. {ahead} ahead, the chat is already humming."
                return f"{provider} is queued. Ahead of you: {ahead}."

            if context.chaos_enabled:
                return f"{provider} accepted. Warming up the download path."
            return f"Got {provider}. I will start downloading soon."

        if joined_existing:
            if context.chaos_enabled:
                return (
                    f"{provider} уже в работе. Повтор засчитан, сидим рядом с таймером."
                )
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
        if context.language_code == "en":
            if context.chaos_enabled:
                return f"{context.provider_label}: fetching media, stay close."
            return f"{context.provider_label}: downloading."
        if context.chaos_enabled:
            return f"{context.provider_label}: пошла добыча, не моргаем."
        return f"{context.provider_label}: скачиваю."

    @staticmethod
    def cancelled(chaos_enabled: bool, language_code: str = "ru") -> str:
        if language_code == "en":
            if chaos_enabled:
                return "Request cancelled. The drama ended before the download."
            return "Request cancelled."
        if chaos_enabled:
            return "Запрос отменен. Драма закончилась раньше скачивания."
        return "Запрос отменен."

    @staticmethod
    def failed(chaos_enabled: bool, language_code: str = "ru") -> str:
        if language_code == "en":
            if chaos_enabled:
                return "The download failed. I will sort out what happened."
            return "Could not download media."
        if chaos_enabled:
            return "Скачивание упало. Сейчас разберу завалы и скажу, что случилось."
        return "Не удалось скачать медиа."

    @staticmethod
    def unexpected_error(language_code: str = "ru") -> str:
        if language_code == "en":
            return "Something unexpected happened. Try again later."
        return "Произошла неожиданная ошибка. Попробуй позже."

    @staticmethod
    def error(
        error: Exception, *, chaos_enabled: bool, language_code: str = "ru"
    ) -> str:
        error_text = str(error)
        error_lower = error_text.lower()
        if language_code == "en":
            if (
                "authentication failed" in error_lower
                or "cookies have expired" in error_lower
            ):
                return "Instagram authorization failed. The bot owner needs to refresh the session."
            if "rate-limit" in error_lower or "rate limit" in error_lower:
                if chaos_enabled:
                    return "The provider hit a rate limit. Backing off for now."
                return "Provider rate limit reached. Try again later."
            if "unsupported" in error_lower:
                return "This link is not supported."
            if "timed out" in error_lower:
                return "The download took too long. Try again."
            return "Could not download media. Try again later."
        if (
            "authentication failed" in error_lower
            or "cookies have expired" in error_lower
        ):
            return (
                "Не прошла авторизация Instagram. Владельцу бота нужно обновить сессию."
            )
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
    def stats_disabled(language_code: str = "ru") -> str:
        if language_code == "en":
            return "Stats are disabled for this chat."
        return "Статистика выключена для этого чата."

    @staticmethod
    def stats(
        stats: dict[str, Any], *, chaos_enabled: bool, language_code: str = "ru"
    ) -> str:
        empty = "none yet" if language_code == "en" else "пока пусто"
        top_users = (
            ", ".join(f"{name} ({count})" for name, count in stats["top_users"])
            or empty
        )
        top_providers = (
            ", ".join(
                f"{ChaosText.provider_name(provider)} ({count})"
                for provider, count in stats["top_providers"]
            )
            or empty
        )

        if language_code == "en":
            title = "Chaos stats" if chaos_enabled else "Chat stats"
            duplicate_label = (
                "Duplicate links in the same run"
                if chaos_enabled
                else "Duplicate links"
            )
            return (
                f"{title}:\n"
                f"- Successful: {stats['completed']}\n"
                f"- Failed: {stats['failed']}\n"
                f"- Cancelled: {stats['cancelled']}\n"
                f"- From cache: {stats['cache_hits']}\n"
                f"- {duplicate_label}: {stats['duplicate_joins']}\n"
                f"- Top users: {top_users}\n"
                f"- Top providers: {top_providers}"
            )

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
    def status(
        snapshot: dict[str, int],
        persisted: dict[str, int],
        *,
        chaos_enabled: bool,
        language_code: str = "ru",
    ) -> str:
        if language_code == "en":
            title = "Chaos queue status" if chaos_enabled else "Queue status"
            return (
                f"{title}:\n"
                f"- Active jobs: {snapshot['active_jobs']}\n"
                f"- Queued jobs: {snapshot['queued_jobs']}\n"
                f"- Active requests: {snapshot['active_requests']}\n"
                f"- Chat limit: {snapshot['chat_limit']}\n"
                f"- Per-user limit: {snapshot['user_limit']}\n"
                f"- Completed: {persisted['completed']}\n"
                f"- Failed: {persisted['failed']}\n"
                f"- Cache hits: {persisted['cache_hits']}"
            )
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
    def no_active_request(language_code: str = "ru") -> str:
        if language_code == "en":
            return "You do not have any active queued or downloading requests."
        return "У тебя нет активных запросов в очереди или скачивании."

    @staticmethod
    def latest_cancelled(language_code: str = "ru") -> str:
        if language_code == "en":
            return "Latest request cancelled."
        return "Последний запрос отменен."

    @staticmethod
    def too_many_links(limit: int, language_code: str = "ru") -> str:
        if language_code == "en":
            return f"Only the first {limit} supported links will be queued."
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
        return (
            "Inline delivery failed. If this was a one-time payment, it was refunded."
        )

    @staticmethod
    def rate_limited(retry_after_seconds: int, language_code: str = "ru") -> str:
        minutes = max(1, (retry_after_seconds + 59) // 60)
        if language_code == "en":
            return f"Too many requests. Try again in about {minutes} min."
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
        return (
            "Inline refund charge not found. Add user_id to refund an unknown charge."
        )

    @staticmethod
    def inline_refund_already_refunded() -> str:
        return "Inline refund was already recorded for this charge."

    @staticmethod
    def inline_refund_failed() -> str:
        return "Inline refund failed. Check logs and try again."
