/**
 * Runtime config endpoint for the frontend.
 *
 * Reads the backend API URL + key from the server environment at request
 * time, so they can be set as plain env vars (no build-time baking).
 *
 * NOTE: deliberately NOT under /api/* (that path is routed to the backend
 * service on Koyeb). This lives at /config to avoid the routing conflict.
 */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    apiBaseUrl:
      process.env.NEXT_PUBLIC_API_BASE_URL ??
      "https://career-agent-kianwoon-88223cd5.koyeb.app",
    apiKey: process.env.NEXT_PUBLIC_API_KEY ?? "",
    storeUrl: process.env.NEXT_PUBLIC_EXTENSION_STORE_URL ?? "",
  });
}
