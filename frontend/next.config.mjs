import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Pin tracing to this project so Next doesn't infer a parent workspace
  // (e.g. when this repo lives inside a larger folder with its own lockfile).
  outputFileTracingRoot: __dirname,
};

export default nextConfig;
