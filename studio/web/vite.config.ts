import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.LEARNING_API || "http://127.0.0.1:8700",
        timeout: 300000,
        proxyTimeout: 300000,
      },
    },
  },
});
