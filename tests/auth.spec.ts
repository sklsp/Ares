import { expect, test } from "@playwright/test";

const password = "long enough password";

test("login grants access to the operations console", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;
  await page.request.post("http://localhost:8000/auth/register", {
    data: { email, password, role: "manager" },
  });

  await page.goto("/");
  // Unauthenticated users are redirected to the login screen.
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /Sign in/ }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening), manager\.$/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What should we investigate?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Product pulse" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Market opportunity feed" })).toBeVisible();
  await expect(page.locator("#catalog").getByText("Velocity Pro 5 Road Running Shoe")).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
});

test("session survives a page refresh", async ({ page }) => {
  const email = `refresh-${Date.now()}@example.com`;
  await page.request.post("http://localhost:8000/auth/register", {
    data: { email, password, role: "viewer" },
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /Sign in/ }).click();
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening), viewer\.$/ })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening), viewer\.$/ })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  const email = `bad-${Date.now()}@example.com`;
  await page.request.post("http://localhost:8000/auth/register", {
    data: { email, password, role: "viewer" },
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("totally wrong password");
  await page.getByRole("button", { name: /Sign in/ }).click();

  await expect(page.getByText("Invalid credentials")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("sign out returns to login and clears access", async ({ page }) => {
  const email = `logout-${Date.now()}@example.com`;
  await page.request.post("http://localhost:8000/auth/register", {
    data: { email, password, role: "viewer" },
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /Sign in/ }).click();
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening), viewer\.$/ })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});
