# Sharwa AI WhatsApp Gateway - standalone container.
# Published only on 127.0.0.1 of the Sharwa server (never the public internet).

FROM node:24-alpine

# Run as a non-root user.
RUN addgroup -S app && adduser -S app -G app

WORKDIR /app

# Install production dependencies only.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy application source.
COPY src ./src

ENV NODE_ENV=production \
    PORT=4001

# Persistent WhatsApp credentials (auth_sessions/) survive container restarts.
VOLUME ["/app/auth_sessions"]

# Deliberately different from SharwaConnect's 4000 to avoid any port clash if
# both ever run on the same host. Override at runtime with `-e PORT=...`.
EXPOSE ${PORT}

USER app

CMD ["node", "src/index.js"]
