import { defineConfig } from 'vite';

export default defineConfig({
  base: '/PMRDA_landslide/',
  server: {
    port: 5173,
  },
  preview: {
    port: 4173,
  },
});
