import { expect, type APIRequestContext } from "@playwright/test";

const API = "http://localhost:8000";
// The local admin that scripts/start.ps1 and CI create with `python -m app.create_user`.
const ADMIN = {
  email: process.env.ARES_ADMIN_EMAIL ?? "admin@ares.local",
  password: process.env.ARES_ADMIN_PASSWORD ?? "ares-local-admin",
};

/** Public sign-up only creates viewers, so accounts with other roles are made by the admin. */
export async function createUser(request: APIRequestContext, email: string, password: string, role: string) {
  const login = await request.post(`${API}/auth/login`, { data: ADMIN });
  expect(login.ok(), "the local admin exists (python -m app.create_user admin@ares.local --role admin)").toBeTruthy();
  const { access_token } = await login.json();
  const created = await request.post(`${API}/auth/register`, {
    data: { email, password, role },
    headers: { Authorization: `Bearer ${access_token}` },
  });
  expect(created.status()).toBe(201);
}
