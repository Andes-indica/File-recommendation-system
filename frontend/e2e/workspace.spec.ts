import { test, expect } from "@playwright/test";

test("upload, search, follow up, preview, feedback, and preferences", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Your files, within reach." }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/folio-home-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles([
    {
      name: "orchid-project.md",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "# Orchid project\nMigration milestones, deployment planning, and release readiness.",
      ),
    },
    {
      name: "garden-guide.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "Tulip garden soil and plant watering recommendations.",
      ),
    },
  ]);
  await expect(page.getByText(/2 files added/)).toBeVisible();
  await expect(page.getByText("2", { exact: true }).first()).toBeVisible({
    timeout: 20_000,
  });
  await page
    .getByRole("textbox", { name: "Search your files" })
    .fill("orchid migration");
  await page.getByRole("button", { name: "Find files" }).click();
  await expect(
    page.getByRole("heading", { name: /recommended file/ }),
  ).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Preview orchid-project.md" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page
      .getByText(
        "Migration milestones, deployment planning, and release readiness.",
        { exact: false },
      )
      .last(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Close preview", exact: true })
    .last()
    .click();
  await page
    .getByRole("button", { name: "Mark orchid-project.md relevant" })
    .click();
  await expect(page.getByText(/Feedback saved/)).toBeVisible();
  await page
    .getByRole("textbox", { name: "Search your files" })
    .fill("only PDFs");
  await page.getByRole("button", { name: "Find files" }).click();
  await expect(
    page.getByRole("heading", { name: "No matching files", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Local intelligence" }),
  ).toBeVisible();
  await page
    .getByRole("switch", { name: "Personalized recommendations" })
    .click();
  await expect(
    page.getByRole("switch", { name: "Personalized recommendations" }),
  ).toHaveAttribute("aria-checked", "false");
  await page.screenshot({
    path: "/tmp/folio-settings-desktop.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("mobile navigation and library fit the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Your files, within reach." }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/folio-home-mobile.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "File library", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your file library." }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
