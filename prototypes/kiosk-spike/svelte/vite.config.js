// SPIKE #71 candidate B: one HTML entry per kiosk page, built to dist/ and
// served by Django at /spike/b/ as static files.  Node runs only here.
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  base: '/spike/b/',
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        'now-racing': 'now-racing.html',
        standings: 'standings.html',
        leaders: 'leaders.html',
      },
    },
  },
});
