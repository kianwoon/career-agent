/**
 * Runtime configuration for the API client.
 *
 * Reads the backend URL + API key from a runtime config endpoint (`/config`)
 * that the Next.js server exposes. The server reads its own environment at
 * request time, so these values can be set as regular env vars on the host
 * (no build-time baking needed — works on Koyeb).
 *
 * Falls back to `NEXT_PUBLIC_*` build-time values (or localhost) if the
 * runtime endpoint is unavailable (e.g. static export / dev).
 */
"use client";

export interface RuntimeConfig {
  apiBaseUrl: string;
  apiKey: string;
}

let cached: RuntimeConfig | null = null;
let loading: Promise<RuntimeConfig> | null = null;

async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  try {
    const res = await fetch("/config", { cache: "no-store" });
    if (res.ok) {
      const data = (await res.json()) as RuntimeConfig;
      if (data.apiBaseUrl) return data;
    }
  } catch {
    // ignore — fall through to env defaults
  }
  return {
    apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    apiKey: process.env.NEXT_PUBLIC_API_KEY ?? "",
  };
}

/** Get runtime config (cached per page load). */
export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;
  if (!loading) {
    loading = fetchRuntimeConfig().then((cfg) => {
      cached = cfg;
      return cfg;
    });
  }
  return loading;
}

export async function apiBaseUrl(): Promise<string> {
  return (await getRuntimeConfig()).apiBaseUrl;
}

export async function apiKey(): Promise<string> {
  return (await getRuntimeConfig()).apiKey;
}
