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
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    apiKey: process.env.NEXT_PUBLIC_API_KEY ?? "",
  });
}
