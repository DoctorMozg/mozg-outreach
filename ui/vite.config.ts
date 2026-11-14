import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, Vite serves the UI and forwards /api (SSE included) to the Python service.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8742" } },
  build: { outDir: "dist", emptyOutDir: true },
});
