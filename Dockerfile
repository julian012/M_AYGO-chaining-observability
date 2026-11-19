# syntax=docker/dockerfile:1
# Una sola imagen para todas las capas; cada servicio elige su módulo con `command`.
FROM python:3.12-slim-bookworm AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip==24.3.1 \
 && pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Usuario sin privilegios; /var/log/app y /srv/ftp le pertenecen para que los volúmenes nombrados hereden el dueño.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /var/log/app /srv/ftp && chown app:app /var/log/app /srv/ftp
COPY --from=build /install /usr/local
USER 10001:10001
WORKDIR /tmp
LABEL org.opencontainers.image.title="obsproto" \
      org.opencontainers.image.description="Prototipo de correlación de trazas por encadenamiento de IDs" \
      org.opencontainers.image.licenses="MIT"
CMD ["python", "-m", "obsproto.gateway"]
