import { expect, test } from "@playwright/test";

const password = "long enough password";

// The console requires authentication; each test signs in as a fresh manager.
async function signIn(page: import("@playwright/test").Page) {
  const email = `smoke-${Date.now()}-${Math.random().toString(36).slice(2)}@example.com`;
  await page.request.post("http://localhost:8000/auth/register", {
    data: { email, password, role: "manager" },
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /Sign in/ }).click();
  await expect(page.getByRole("heading", { name: "Good morning, manager." })).toBeVisible();
}

test("home page renders for an authenticated manager", async ({ page }) => {
  await signIn(page);

  await expect(page).toHaveTitle(/Ops Agent/);
  await expect(page.getByRole("heading", { name: "What should we investigate?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Product pulse" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Market opportunity feed" })).toBeVisible();
  await expect(page.getByPlaceholder("Research a market, niche, or product category")).toBeVisible();
  await expect(page.locator("#catalog").getByText("Velocity Pro 5 Road Running Shoe")).toBeVisible();
});
