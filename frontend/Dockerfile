# frontend/Dockerfile
#
# YOUR STRUCTURE:
#   frontend/
#   ├── Dockerfile     ← this file
#   ├── package.json
#   └── src/
#
# For development, docker-compose overrides CMD to `npm run dev`
# For production, use `npm run build && npm start`

FROM node:20-alpine

WORKDIR /app

# Copy package files first — Docker caches this layer separately.
# npm ci only reruns when package.json/lock changes, saving time.
COPY package.json package-lock.json* ./

RUN npm ci --legacy-peer-deps

# Copy rest of source
COPY . .

EXPOSE 3000

ENV PORT=3000
ENV NEXT_TELEMETRY_DISABLED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD wget -qO- http://localhost:3000/health 2>/dev/null || curl -sf http://localhost:3000 || exit 1

# Default: development with hot reload
# docker-compose overrides this to `npm run dev`
CMD ["npm", "run", "dev"]