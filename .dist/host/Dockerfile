FROM php:8.5-cli-alpine
COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
RUN apk add --no-cache git unzip python3
WORKDIR /var/www/html
