import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The FastAPI server (uvicorn rag.api.app:app). Use 127.0.0.1, not localhost: on Windows localhost tries IPv6 first.
const API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // The app calls the API with relative URLs; in development Vite forwards these paths to uvicorn,
    // so no CORS setup is needed and the same URLs work when the API serves the built files later.
    proxy: {
      "/health": API,
      "/chat": API,
      "/documents": API,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
  },
});
