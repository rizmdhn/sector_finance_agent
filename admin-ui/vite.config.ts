import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Dev-only: mirrors what admin-ui/nginx.conf.template does in the built Docker
  // image (proxy /api -> the gateway, inject the bearer key server-side so the
  // browser never sees it) — see admin-ui/.env.example and README.md.
  const env = loadEnv(mode, process.cwd(), "");
  const gatewayTarget = env.GATEWAY_URL || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: gatewayTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          headers: env.IDX_GATEWAY_KEY ? { Authorization: `Bearer ${env.IDX_GATEWAY_KEY}` } : undefined,
        },
      },
    },
  };
});
