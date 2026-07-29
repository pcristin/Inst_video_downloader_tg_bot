#define _GNU_SOURCE

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_SECRET_BYTES 4096

static void clear_buffer(char *buffer, size_t length) {
    volatile unsigned char *cursor = (volatile unsigned char *)buffer;
    while (length-- > 0) {
        *cursor++ = 0;
    }
}

static int read_secret(const char *path, char *buffer, size_t capacity) {
    struct stat metadata;
    size_t total = 0;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);

    if (descriptor < 0) {
        return -1;
    }
    if (fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_size < 1 || metadata.st_size > MAX_SECRET_BYTES) {
        close(descriptor);
        return -1;
    }

    while (total < capacity - 1) {
        ssize_t count = read(descriptor, buffer + total, capacity - 1 - total);
        if (count < 0) {
            close(descriptor);
            clear_buffer(buffer, capacity);
            return -1;
        }
        if (count == 0) {
            break;
        }
        total += (size_t)count;
    }
    if (close(descriptor) != 0 || total == 0 || total > MAX_SECRET_BYTES) {
        clear_buffer(buffer, capacity);
        return -1;
    }

    if (buffer[total - 1] == '\n') {
        total--;
    }
    if (total == 0 || memchr(buffer, '\n', total) != NULL ||
        memchr(buffer, '\r', total) != NULL || memchr(buffer, '\0', total) != NULL) {
        clear_buffer(buffer, capacity);
        return -1;
    }
    buffer[total] = '\0';
    return (int)total;
}

int main(int argc, char **argv) {
    char api_id[MAX_SECRET_BYTES + 1] = {0};
    char api_hash[MAX_SECRET_BYTES + 1] = {0};
    struct stat media_metadata;
    const char *api_id_file = getenv("TELEGRAM_API_ID_FILE");
    const char *api_hash_file = getenv("TELEGRAM_API_HASH_FILE");
    const char *api_binary = getenv("TELEGRAM_BOT_API_BINARY");
    const char *media_dir = getenv("TELEGRAM_MEDIA_DIR");
    char **child_argv;
    int index;

    if (api_id_file == NULL || *api_id_file == '\0') {
        api_id_file = "/run/secrets/telegram_api_id";
    }
    if (api_hash_file == NULL || *api_hash_file == '\0') {
        api_hash_file = "/run/secrets/telegram_api_hash";
    }
    if (api_binary == NULL || *api_binary == '\0') {
        api_binary = "telegram-bot-api";
    }
    if (media_dir == NULL || *media_dir == '\0') {
        media_dir = "/app/temp";
    }

    if (stat(media_dir, &media_metadata) != 0 ||
        !S_ISDIR(media_metadata.st_mode) || access(media_dir, R_OK | X_OK) != 0) {
        fputs("Unable to access shared media directory\n", stderr);
        return 1;
    }

    if (read_secret(api_id_file, api_id, sizeof(api_id)) < 0 ||
        read_secret(api_hash_file, api_hash, sizeof(api_hash)) < 0) {
        clear_buffer(api_id, sizeof(api_id));
        clear_buffer(api_hash, sizeof(api_hash));
        fputs("Unable to load Telegram API credential file\n", stderr);
        return 1;
    }

    if (setenv("TELEGRAM_API_ID", api_id, 1) != 0 ||
        setenv("TELEGRAM_API_HASH", api_hash, 1) != 0) {
        clear_buffer(api_id, sizeof(api_id));
        clear_buffer(api_hash, sizeof(api_hash));
        fputs("Unable to prepare Telegram API credentials\n", stderr);
        return 1;
    }
    clear_buffer(api_id, sizeof(api_id));
    clear_buffer(api_hash, sizeof(api_hash));

    child_argv = calloc((size_t)argc + 1, sizeof(*child_argv));
    if (child_argv == NULL) {
        fputs("Unable to prepare Telegram Bot API process\n", stderr);
        return 1;
    }
    child_argv[0] = (char *)api_binary;
    for (index = 1; index < argc; index++) {
        child_argv[index] = argv[index];
    }

    execvp(api_binary, child_argv);
    fputs("Unable to execute Telegram Bot API process\n", stderr);
    free(child_argv);
    return 1;
}
