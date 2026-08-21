import { expect, test } from "@playwright/test";

test("home page renders", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/Ops Agent/);
  await expect(page.getByRole("heading", { name: "Good morning, manager." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What should we investigate?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Product pulse" })).toBeVisible();
  await expect(page.locator("#catalog").getByText("Velocity Pro 5 Road Running Shoe")).toBeVisible();
});
