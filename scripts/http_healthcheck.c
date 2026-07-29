#define _POSIX_C_SOURCE 200112L

#include <netdb.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int probe(const char *host, const char *port) {
    static const char request[] =
        "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    struct addrinfo hints = {0};
    struct addrinfo *addresses = NULL;
    struct addrinfo *address;
    struct timeval timeout = {.tv_sec = 3, .tv_usec = 0};
    char response[5];
    size_t received = 0;
    int descriptor = -1;
    int result = 1;

    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port, &hints, &addresses) != 0) {
        return 1;
    }

    for (address = addresses; address != NULL; address = address->ai_next) {
        descriptor = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (descriptor < 0) {
            continue;
        }
        (void)setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        (void)setsockopt(descriptor, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        if (connect(descriptor, address->ai_addr, address->ai_addrlen) == 0) {
            break;
        }
        close(descriptor);
        descriptor = -1;
    }
    freeaddrinfo(addresses);
    if (descriptor < 0) {
        return 1;
    }

    if (send(descriptor, request, sizeof(request) - 1, 0) != (ssize_t)(sizeof(request) - 1)) {
        close(descriptor);
        return 1;
    }
    while (received < sizeof(response)) {
        ssize_t count = recv(descriptor, response + received, sizeof(response) - received, 0);
        if (count <= 0) {
            close(descriptor);
            return 1;
        }
        received += (size_t)count;
    }
    if (memcmp(response, "HTTP/", sizeof(response)) == 0) {
        result = 0;
    }
    close(descriptor);
    return result;
}

int main(int argc, char **argv) {
    const char *host = argc > 1 ? argv[1] : "127.0.0.1";
    const char *port = argc > 2 ? argv[2] : "8081";

    if (argc > 3) {
        return 1;
    }
    return probe(host, port);
}
