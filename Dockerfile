FROM php:8.2-apache

ENV DERBYNET_CONFIG_DIR=/var/lib/derbynet/config \
    APACHE_RUN_USER=www-data \
    APACHE_RUN_GROUP=www-data \
    APACHE_LOG_DIR=/var/log/apache2

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        libfreetype6-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libzip-dev \
        libicu-dev \
        unzip; \
    rm -rf /var/lib/apt/lists/*; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" gd pdo_mysql pdo_sqlite zip intl; \
    a2enmod rewrite headers; \
    mkdir -p /var/lib/derbynet/config; \
    chown -R www-data:www-data /var/lib/derbynet

COPY docker/apache-default.conf /etc/apache2/conf-available/derbynet.conf
RUN a2enconf derbynet

WORKDIR /var/www/html
COPY website/ /var/www/html/
RUN find /var/www/html -type d -exec chmod 755 {} \; && \
    find /var/www/html -type f -exec chmod 644 {} \; && \
    chown -R www-data:www-data /var/www/html

VOLUME ["/var/lib/derbynet"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s CMD curl -fsS http://localhost/ || exit 1
