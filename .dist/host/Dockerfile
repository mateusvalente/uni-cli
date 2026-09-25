FROM php:8.5-cli-alpine
COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
RUN apk add --no-cache git unzip python3 py3-pip \
    && python3 -m venv /opt/asset-tools \
    && /opt/asset-tools/bin/pip install --no-cache-dir jsbeautifier==1.15.4 cssbeautifier==1.15.4
ENV PATH="/opt/asset-tools/bin:${PATH}"
WORKDIR /var/www/html
