import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: '#0b0d10',
          subtle: '#11141a',
          raised: '#1a1d22',
        },
        border: {
          DEFAULT: '#262a31',
          subtle: '#1f2229',
        },
        fg: {
          DEFAULT: '#e6e6e6',
          muted: '#9ba1ab',
          subtle: '#6b7280',
        },
        accent: {
          DEFAULT: '#3b82f6',
          hover: '#2563eb',
        },
        tier: {
          green: '#22c55e',
          yellow: '#eab308',
          red: '#ef4444',
        },
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
