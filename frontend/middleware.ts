/**
 * Temporary password gate for the deployed Career Agent site.
 *
 * Uses a server-side SITE_PASSWORD env var (never exposed to the client).
 * If SITE_PASSWORD is unset, the site is open (dev mode). If set, visitors
 * must enter the password; a signed cookie then allows access.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SITE_PASSWORD = process.env.SITE_PASSWORD ?? "";
const AUTH_COOKIE = "site_auth";
const AUTH_VALUE = "ok";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7; // 7 days

export function middleware(request: NextRequest) {
  // No password configured -> open site.
  if (!SITE_PASSWORD) {
    return NextResponse.next();
  }

  const { pathname } = request.nextUrl;

  // Allow the login page itself and static assets.
  if (
    pathname === "/login" ||
    pathname.startsWith("/_next/") ||
    pathname.startsWith("/favicon") ||
    pathname === "/config" ||
    pathname.startsWith("/api/")
  ) {
    return NextResponse.next();
  }

  // Already authenticated -> allow.
  const cookie = request.cookies.get(AUTH_COOKIE);
  if (cookie?.value === AUTH_VALUE) {
    return NextResponse.next();
  }

  // Not authenticated -> redirect to login.
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|login).*)"],
};
