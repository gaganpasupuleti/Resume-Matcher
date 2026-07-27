import type { NextConfig } from 'next';

const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8000';

const DEFAULT_FRAME_ANCESTORS = [
  'http://localhost:5000',
  'http://127.0.0.1:5000',
];

function frameAncestorsCsp(): string {
  const fromEnv = (process.env.NEXT_PUBLIC_CODEQUEST_EMBED_PARENT_ORIGINS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const origins = fromEnv.length > 0 ? fromEnv : DEFAULT_FRAME_ANCESTORS;
  return `frame-ancestors 'self' ${origins.join(' ')}`;
}

const nextConfig: NextConfig = {
  output: 'standalone',
  experimental: {
    turbopackUseSystemTlsCerts: true,
    // Local Ollama resume parsing can take several minutes
    proxyTimeout: 900_000,
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: frameAncestorsCsp(),
          },
        ],
      },
    ];
  },
  async rewrites() {
    // Note: Next.js serves filesystem routes (app/api/) before rewrites.
    // Do not create app/api/ routes or they will shadow the backend proxy.
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_ORIGIN}/api/:path*`,
      },
      {
        source: '/docs',
        destination: `${BACKEND_ORIGIN}/docs`,
      },
      {
        source: '/redoc',
        destination: `${BACKEND_ORIGIN}/redoc`,
      },
      {
        source: '/openapi.json',
        destination: `${BACKEND_ORIGIN}/openapi.json`,
      },
    ];
  },
};

export default nextConfig;
