/** @type {import('next').NextConfig} */

/**
 * frontend/next.config.js
 *
 * Next.js proxies all backend calls through rewrites.
 * This means the browser thinks everything is on localhost:3000,
 * so there are no CORS issues in development.
 *
 * WebSocket (WS) cannot be proxied by Next.js rewrites —
 * the browser connects directly via NEXT_PUBLIC_WS_URL.
 */

const nextConfig = {
  async rewrites() {
    // BACKEND_URL is set in docker-compose.yml environment
    // Default to localhost:8000 for local dev without Docker
    const backend = process.env.BACKEND_URL || "http://localhost:8000";

    return [
      { source: "/api/:path*",    destination: `${backend}/api/:path*`    },
      { source: "/auth/:path*",   destination: `${backend}/auth/:path*`   },
      { source: "/users/:path*",  destination: `${backend}/users/:path*`  },
      { source: "/health",        destination: `${backend}/health`         },
    ];
  },
};

module.exports = nextConfig;