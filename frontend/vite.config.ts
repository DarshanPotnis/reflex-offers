import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built straight into backend/static, which FastAPI serves with an
// index.html fallback so /offers/{id} survives a hard refresh.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
