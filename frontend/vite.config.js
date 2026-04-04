import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/state": "http://127.0.0.1:8000",
      "/inject": "http://127.0.0.1:8000",
    },
  },
});
