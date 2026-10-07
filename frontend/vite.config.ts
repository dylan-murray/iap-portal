import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const backend = "http://localhost:8088";

export default defineConfig({
  plugins: [vue()],
  build: {
    manifest: true,
    rollupOptions: { input: { main: "index.html", annotations: "src/dev-annotator.ts" } },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/login": backend,
      "/logout": backend,
      "/auth": backend,
      "/dev": backend,
      "/.well-known": backend,
    },
  },
});
