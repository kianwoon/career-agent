import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const SITE_PASSWORD = process.env.SITE_PASSWORD ?? "";
const AUTH_COOKIE = "site_auth";
const AUTH_VALUE = "ok";

async function handleLogin(formData: FormData) {
  "use server";
  const password = formData.get("password")?.toString() ?? "";
  const next = formData.get("next")?.toString() ?? "/";

  if (SITE_PASSWORD && password === SITE_PASSWORD) {
    (await cookies()).set(AUTH_COOKIE, AUTH_VALUE, {
      httpOnly: true,
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 7,
      path: "/",
    });
    redirect(next.startsWith("/") ? next : "/");
  }
  redirect(`/login?next=${encodeURIComponent(next)}&error=1`);
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = await searchParams;
  // No password configured -> open site, skip login.
  if (!SITE_PASSWORD) {
    redirect("/");
  }

  // Already authenticated -> skip login.
  const cookieStore = cookies();
  const authed = (await cookieStore).get(AUTH_COOKIE)?.value === AUTH_VALUE;
  if (authed) {
    redirect(params.next ?? "/");
  }

  const next = params.next ?? "/";
  const error = params.error === "1";

  return (
    <div className="login-page">
      <form className="login-card" action={handleLogin}>
        <h1>Career Agent</h1>
        <p className="login-sub">This site is password-protected.</p>
        <input type="hidden" name="next" value={next} />
        <input
          type="password"
          name="password"
          placeholder="Enter password"
          autoFocus
          required
        />
        {error && <p className="login-error">Incorrect password. Try again.</p>}
        <button type="submit">Enter</button>
      </form>
    </div>
  );
}
