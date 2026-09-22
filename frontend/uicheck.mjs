/**
 * Drives the real UI in a browser for the Phase 3 manual check.
 *
 * Expected values are read from the API rather than written in here, so the
 * script cannot pass by agreeing with itself.
 *
 * Usage: node uicheck.mjs http://localhost:8742 [screenshot-dir]
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";
import path from "node:path";

const BASE = process.argv[2] ?? "http://localhost:8742";
const here = path.dirname(fileURLToPath(import.meta.url));
// Screenshots for docs/failure-demo.md. Written only when a directory is given.
const SHOTS = process.argv[3] ? path.resolve(process.argv[3]) : null;
const FIXTURE = path.join(here, "../backend/tests/fixtures/01-northstar-line-sheet.xlsx");

let failures = 0;
const ok = (label, extra = "") => console.log(`  PASS  ${label}${extra ? ` — ${extra}` : ""}`);
const bad = (label, detail) => {
  failures += 1;
  console.log(`  FAIL  ${label} — ${detail}`);
};
function check(label, condition, detail = "") {
  condition ? ok(label, detail) : bad(label, detail || "condition was false");
}
const step = (name) => console.log(`\n${name}`);

const api = async (p) => (await fetch(`${BASE}${p}`)).json();

async function shot(page, name) {
  if (!SHOTS) return;
  await fs.mkdir(SHOTS, { recursive: true });
  // Top of the page, so the totals and the save bar are in the same frame:
  // the point of each shot is what the user is told and what it cost.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(150);
  const file = path.join(SHOTS, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  SHOT  ${path.relative(process.cwd(), file)}`);
}

// The whole page in one image. The save bar is position: fixed, and a plain
// fullPage capture paints it over the middle of the page, so the viewport is
// made as tall as the document for the shot and then put back.
async function shotFull(page, name) {
  if (!SHOTS) return;
  await fs.mkdir(SHOTS, { recursive: true });
  const size = page.viewportSize();
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: size.width, height });
  await page.waitForTimeout(200);
  const file = path.join(SHOTS, `${name}.png`);
  await page.screenshot({ path: file });
  await page.setViewportSize(size);
  console.log(`  SHOT  ${path.relative(process.cwd(), file)}`);
}

const count = (text) => Number(String(text).replace(/,/g, ""));
const asked = (offer) =>
  offer.lines.filter((l) => l.issues.some((i) => i.kind === "needs_decision")).length;
const leftOut = (offer) =>
  offer.lines.filter((l) => l.status === "excluded" && !l.needs_decision_open);

// The summary panel's top level must reconcile to the lines read:
// ready + left out + need your decision. "Cleaned up" and "worth a look" sit
// inside ready and count ready lines only. Every number is checked against
// the API, and "lines read" against both the sentence and the API.
async function checkSummaryReconciles(p, offer, name) {
  const lead = await p.locator(".overview-lead").innerText();
  const read = count(lead.match(/^Read ([\d,]+) lines?/)[1]);
  const top = async (kind) =>
    count(await p.locator(`.chip[data-kind="${kind}"] > b`).innerText());
  const within = async (kind) => {
    const el = p.locator(`.chip[data-kind="ready"] [data-kind="${kind}"] > b`);
    return (await el.count()) === 0 ? 0 : count(await el.innerText());
  };
  const ready = await top("ready");
  const out = await top("left-out");
  const needs = await top("needs");
  check(`${name}: ready + left out + need your decision == lines read`,
    ready + out + needs === read && read === offer.summary.total_lines,
    `${ready} + ${out} + ${needs} = ${ready + out + needs}; read ${read}; ` +
      `API ${offer.summary.total_lines}`);
  check(`${name}: each top-level number matches the API`,
    ready === offer.summary.included_lines &&
      out === leftOut(offer).length &&
      needs === offer.summary.needs_decision_open,
    `ready ${offer.summary.included_lines}, left out ${leftOut(offer).length}, ` +
      `open ${offer.summary.needs_decision_open}`);
  const readyLines = offer.lines.filter((l) => l.status === "included");
  const has = (l, kind) => l.issues.some((i) => i.kind === kind);
  const cleaned = readyLines.filter((l) => has(l, "fixed")).length;
  const worth = readyLines.filter((l) => has(l, "warning")).length;
  const shownCleaned = await within("fixed");
  const shownWorth = await within("warning");
  check(`${name}: cleaned up and worth a look count ready lines only (API)`,
    shownCleaned === cleaned && shownWorth === worth &&
      shownCleaned <= ready && shownWorth <= ready,
    `${shownCleaned} cleaned up, ${shownWorth} worth a look, within ${ready} ready`);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 940 } });
const page = await context.newPage();
page.on("pageerror", (e) => bad("no page errors", String(e)));

try {
  // ---------------------------------------------------------------- upload
  step("0. The upload page says what it takes and what happens next");
  await page.goto(`${BASE}/`);
  await page.waitForSelector(".dropzone");
  check("a drop zone with a file button",
    (await page.locator(".dropzone").isVisible()) &&
      (await page.getByRole("button", { name: "Choose a file" }).isVisible()));
  const layoutNames = await page.locator(".layout h3").allInnerTexts();
  check("both supported layouts are named",
    layoutNames.join(",") === "Northstar,Harbor", layoutNames.join(", "));
  check("three steps under 'What happens next'",
    (await page.locator(".steps li").count()) === 3);
  await shotFull(page, "ui-1-upload");

  step("1. Upload from the home page");
  await page.setInputFiles('input[type="file"]', FIXTURE);
  await page.waitForURL(/\/offers\/.+/, { timeout: 15000 });
  const offerId = page.url().split("/offers/")[1];
  const loaded = await api(`/api/offers/${offerId}`);
  check("landed on the offer page", Boolean(offerId));

  const costOnScreen = await page.locator(".total-cost .total-figure").innerText();
  const retailOnScreen = await page.locator(".total-retail .total-figure").innerText();
  check(
    "supplier total on screen matches the API",
    costOnScreen.replace(/[$,]/g, "") === loaded.summary.supplier_cost,
    `screen ${costOnScreen} / api ${loaded.summary.supplier_cost}`,
  );
  check(
    "retail shown separately and labelled",
    (await page.locator(".total-retail").innerText()).includes("Not what we pay"),
    retailOnScreen,
  );
  check(
    "export is enabled with nothing unsaved",
    await page.getByRole("button", { name: "Export Excel" }).isEnabled(),
  );
  check(
    "the export button downloads .xlsx, not .csv",
    (await page.locator('a[href*="/export."]').first().getAttribute("href")).endsWith(".xlsx"),
  );
  check("the CSV is still offered alongside it",
    (await page.locator('a[href$="/export.csv"]').count()) === 1);

  step("1a. The summary panel says what was done and what is needed");
  const lead = await page.locator(".overview-lead").innerText();
  check("it names the file and the number of lines",
    lead === `Read ${loaded.summary.total_lines.toLocaleString("en-US")} lines from ${loaded.source_filename}.`,
    lead);
  const chip = async (kind) =>
    count(await page.locator(`.chip[data-kind="${kind}"] > b`).innerText());
  await checkSummaryReconciles(page, loaded, "Northstar");
  const readyNow = loaded.lines.filter((l) => l.status === "included");
  const readyText = `${loaded.summary.included_lines} ready to go (` +
    `${readyNow.filter((l) => l.issues.some((i) => i.kind === "fixed")).length} cleaned up automatically, ` +
    `${readyNow.filter((l) => l.issues.some((i) => i.kind === "warning")).length} worth a look)`;
  const readyShown = await page.locator('.chip[data-kind="ready"]').innerText();
  check("the ready count carries its cleaned-up and worth-a-look lines inside it",
    readyShown === readyText, readyShown);
  if (leftOut(loaded).some((l) => l.issues.some((i) => i.code === "ZERO_QUANTITY"))) {
    const leftOutChip = await page.locator('.chip[data-kind="left-out"]').innerText();
    check("the reason for leaving lines out is named", leftOutChip.includes("(0 pieces)"),
      leftOutChip);
  }
  const progress = await page.locator(".decisions-count").innerText();
  check("progress starts at 0 of every question the sheet raised (API)",
    progress.startsWith(`0 of ${asked(loaded)} decided`), progress);
  check("the progress bar says the same",
    (await page.locator('[role="progressbar"]').getAttribute("aria-valuenow")) === "0" &&
      (await page.locator('[role="progressbar"]').getAttribute("aria-valuemax")) ===
        String(asked(loaded)));

  step("1b. The list explains itself without expanding anything");
  await page.getByRole("tab", { name: /Excluded/ }).click();
  const headers = (await page.locator(".line-header").first().innerText())
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const wanted = ["Item", "Description", "Size", "Pieces", "Supplier cost", "Line value", "Status"];
  // innerText returns the CSS-uppercased text, so compare case-insensitively.
  const seen = headers.map((h) => h.toLowerCase());
  check("column headers are present",
    wanted.every((h) => seen.includes(h.toLowerCase())),
    headers.join(" | "));

  // Expected reasons are derived from the API, not written in here.
  const reasonFor = (code) => loaded.lines.find((l) => l.issues.some((i) => i.code === code));
  const expected = [];
  const push = (code, text) => { if (reasonFor(code)) expected.push([code, text]); };
  push("MISSING_COST", "No supplier cost");
  push("ZERO_QUANTITY", "0 pieces");
  push("MISSING_ITEM_CODE", "No item code");
  const neg = reasonFor("NEGATIVE_QUANTITY");
  if (neg) expected.push(["NEGATIVE_QUANTITY", `Quantity is ${neg.original.quantity.value}`]);
  const badQty = reasonFor("INVALID_QUANTITY");
  if (badQty) expected.push(["INVALID_QUANTITY",
    `Quantity '${String(badQty.cells.quantity.raw).trim()}' isn't a number`]);
  const dup = reasonFor("DUPLICATE_ROW");
  if (dup) {
    const other = loaded.lines.find((l) => l.line_id === dup.related_line_ids[0]);
    expected.push(["DUPLICATE_ROW", `Duplicate of row ${other.source_row}`]);
  }
  const clash = reasonFor("CONFLICTING_ROWS");
  if (clash) {
    const other = loaded.lines.find((l) => l.line_id === clash.related_line_ids[0]);
    expected.push(["CONFLICTING_ROWS", `Conflicts with row ${other.source_row}`]);
  }

  const shown = await page.locator(".line-reason").allInnerTexts();
  for (const [code, text] of expected) {
    check(`reason shown in the list: ${code}`, shown.includes(text), text);
  }
  check(
    "every excluded row carries a reason",
    shown.length >= (await page.locator(".line").count()),
    `${shown.length} reasons for ${await page.locator(".line").count()} rows`,
  );
  const badges = await page.locator(".pill.needs").allInnerTexts();
  check("the badge reads 'needs decision'",
    badges.length > 0 && badges.every((b) => b === "needs decision"),
    [...new Set(badges)].join(", "));
  await page.getByRole("tab", { name: /Needs decision/ }).click();

  step("1c. 'Review N decisions' opens the tab that needs them");
  await page.getByRole("tab", { name: /All lines/ }).click();
  const reviewButton = page.getByRole("button", { name: /^Review \d+ decisions?/ });
  const reviewLabel = await reviewButton.innerText();
  check("the button counts the open decisions (API)",
    reviewLabel.startsWith(`Review ${loaded.summary.needs_decision_open} decisions`), reviewLabel);
  await reviewButton.click();
  check("it opens Needs decision",
    (await page.getByRole("tab", { name: /Needs decision/ }).getAttribute("aria-selected")) ===
      "true");

  // ------------------------------------------------------------ conflicts
  step("2. Resolve the conflict group with one save");
  const conflictLine = loaded.lines.find((l) =>
    l.issues.some((i) => i.code === "CONFLICTING_ROWS"),
  );
  const siblings = [conflictLine.line_id, ...conflictLine.related_line_ids];
  check("a conflict group exists", siblings.length > 1, siblings.join(" + "));

  // There is more than one group on this sheet (a duplicate pair as well as
  // the conflict), so select the card by the item code in question.
  const group = page.locator(".group").filter({ hasText: conflictLine.item_code });
  check("the conflict is shown as one card", (await group.count()) === 1,
    `${await page.locator(".group").count()} groups on the page`);
  const firstOption = group.locator(".group-option").first();
  const keptRow = (await firstOption.innerText()).match(/Row (\d+)/)[1];
  await firstOption.getByRole("button", { name: "Keep this one" }).click();

  const stagedAfterKeep = await page.locator(".savebar-message").innerText();
  check(
    "keeping one row stages the whole group",
    stagedAfterKeep.includes(`${siblings.length} unsaved`),
    stagedAfterKeep,
  );
  check(
    "export is blocked while unsaved",
    await page.getByRole("button", { name: "Export Excel" }).isDisabled(),
  );
  const hint = page.locator(".export-hint");
  check("the reason is visible text, not a tooltip", await hint.isVisible(),
    (await hint.innerText()).replace(/\s+/g, " "));
  check(
    "the reason counts the unsaved changes",
    (await hint.innerText()).replace(/\s+/g, " ").includes(`${siblings.length} changes`),
  );
  check("the disabled button carries no title attribute to hide behind",
    (await page.getByRole("button", { name: "Export Excel" }).getAttribute("title")) === null);

  // --------------------------------------------------------- fix a cost
  step("3. Fix the line with no supplier cost");
  const noCost = loaded.lines.find((l) =>
    l.issues.some((i) => i.code === "MISSING_COST"),
  );
  await page.locator(".line-head", { hasText: noCost.item_code }).first().click();
  const detail = page.locator(".line", { hasText: noCost.item_code }).first();

  // Include with the cost still blank: nothing may be staged; the cost is
  // asked for on the line instead of failing at save.
  const costInput = detail.locator('label:has-text("Supplier cost") input');
  check("the cost starts blank, as the sheet had it", (await costInput.inputValue()) === "");
  await detail.getByRole("button", { name: "Include", exact: true }).click();
  const heldBack = await page.locator(".savebar-message").innerText();
  check("Include with no cost stages nothing",
    heldBack.includes(`${siblings.length} unsaved`) &&
      !(await detail.evaluate((el) => el.classList.contains("is-staged"))),
    heldBack);
  const ask = await detail.locator(".ask").innerText();
  check("it asks for the missing cost right there", /enter the supplier cost/.test(ask), ask);
  check("the blank field is flagged and focused",
    (await costInput.getAttribute("aria-invalid")) === "true" &&
      (await costInput.evaluate((el) => el === document.activeElement)));
  await detail.locator('label:has-text("Supplier cost") input').fill("9.00");
  check("the prompt clears once a cost is typed", (await detail.locator(".ask").count()) === 0);
  await detail.getByRole("button", { name: "Include", exact: true }).click();
  check(
    "the fix is staged",
    (await page.locator(".savebar-message").innerText()).includes(
      `${siblings.length + 1} unsaved`,
    ),
  );

  // ------------------------------------------------------------ save
  step("4. Save");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.waitForSelector(".savebar.saved", { timeout: 15000 });
  const savedMessage = await page.locator(".savebar-message").innerText();
  check("save bar reports the new version", savedMessage.includes("Saved — v2"), savedMessage);

  const afterSave = await api(`/api/offers/${offerId}`);
  const costAfter = await page.locator(".total-cost .total-figure").innerText();
  check(
    "screen totals match the server after saving",
    costAfter.replace(/[$,]/g, "") === afterSave.summary.supplier_cost,
    `screen ${costAfter} / api ${afterSave.summary.supplier_cost}`,
  );
  check("one version bump for the whole batch", afterSave.version === 2, `v${afterSave.version}`);
  const decidedNow = asked(afterSave) - afterSave.summary.needs_decision_open;
  const progressNow = await page.locator(".decisions-count").innerText();
  check("progress counts the saved decisions (API)",
    progressNow.startsWith(`${decidedNow} of ${asked(afterSave)} decided`) &&
      (await page.locator('[role="progressbar"]').getAttribute("aria-valuenow")) ===
        String(decidedNow),
    progressNow);
  check("the decision chip follows the save (API)",
    (await chip("needs")) === afterSave.summary.needs_decision_open);

  const siblingStates = siblings.map((id) => {
    const line = afterSave.lines.find((l) => l.line_id === id);
    return `${id}:${line.status}${line.needs_decision_open ? "(open)" : ""}`;
  });
  check(
    "no sibling left open in Needs decision",
    afterSave.lines.filter((l) => siblings.includes(l.line_id) && l.needs_decision_open)
      .length === 0,
    siblingStates.join(" "),
  );
  check(
    `kept row ${keptRow} is included`,
    afterSave.lines.find((l) => l.source_row === Number(keptRow)).status === "included",
  );
  check("export re-enabled once saved",
    await page.getByRole("button", { name: "Export Excel" }).isEnabled());
  check("the reason disappears once saved",
    (await page.locator(".export-hint").count()) === 0);

  // ------------------------------------------- failure, refresh, retry
  step("5. Test mode: fail the next save, refresh mid-failure, then retry");
  check("fault toggle is visible when enabled", await page.locator(".fault-box").isVisible());
  check("the toggle says it is a test affordance",
    (await page.locator(".fault-box").innerText()).includes("Test mode (enabled for this review)"));
  await shot(page, "failure-1-before");
  await page.locator('.fault-box input[type="checkbox"]').check();

  const excludeTarget = afterSave.lines.find(
    (l) => l.status === "included" && !siblings.includes(l.line_id),
  );
  await page.getByRole("tab", { name: /All lines/ }).click();
  await page.locator(".line-head", { hasText: excludeTarget.item_code }).first().click();
  const target = page.locator(".line", { hasText: excludeTarget.item_code }).first();
  await target.getByRole("button", { name: "Exclude", exact: true }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await page.waitForSelector(".savebar.failed", { timeout: 15000 });
  const failMessage = await page.locator(".savebar-message").innerText();
  check("user sees a clear failure", failMessage.includes("Not saved"), failMessage);
  check("Retry offered", await page.getByRole("button", { name: "Retry" }).isVisible());
  await shot(page, "failure-2-failed");

  const duringFailure = await api(`/api/offers/${offerId}`);
  check(
    "the dangerous case: it really did commit",
    duringFailure.version === 3,
    `server is at v${duringFailure.version} while the user was told it failed`,
  );

  step("6. Refresh the page while the save is in the failed state");
  await page.reload();
  await page.waitForSelector(".savebar.failed", { timeout: 15000 });
  const afterReload = await page.locator(".savebar-message").innerText();
  check("failed state survives a refresh", afterReload.includes("Not saved"), afterReload);
  const retryAfterReload = page.getByRole("button", { name: "Retry" });
  check("Retry still available after the refresh", await retryAfterReload.isVisible());
  await shot(page, "failure-3-after-refresh");

  step("7. Retry");
  await retryAfterReload.click();
  await page.waitForSelector(".savebar.saved", { timeout: 15000 });
  const afterRetry = await api(`/api/offers/${offerId}`);
  check(
    "retry applied exactly once — no second version bump",
    afterRetry.version === 3,
    `v${duringFailure.version} before retry, v${afterRetry.version} after`,
  );
  check(
    "the excluded line stayed excluded once",
    afterRetry.lines.find((l) => l.line_id === excludeTarget.line_id).status === "excluded",
  );
  check(
    "totals agree with the server after recovery",
    (await page.locator(".total-cost .total-figure").innerText()).replace(/[$,]/g, "") ===
      afterRetry.summary.supplier_cost,
  );
  await shot(page, "failure-4-after-retry");

  // ------------------------------------------------ persistence + export
  step("8. Reopen in a fresh private window");
  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await otherPage.goto(`${BASE}/offers/${offerId}`);
  await otherPage.waitForSelector(".total-cost .total-figure");
  const elsewhere = await otherPage.locator(".total-cost .total-figure").innerText();
  check(
    "decisions persist for a different browser session",
    elsewhere.replace(/[$,]/g, "") === afterRetry.summary.supplier_cost,
    elsewhere,
  );
  await other.close();

  step("9. Export agrees with the screen");
  const xlsx = await fetch(`${BASE}/api/offers/${offerId}/export.xlsx`);
  const xlsxBytes = Buffer.from(await xlsx.arrayBuffer());
  check("the workbook is a real xlsx",
    xlsxBytes.subarray(0, 2).toString() === "PK" &&
      xlsx.headers.get("content-type").includes("spreadsheetml"),
    `${xlsxBytes.length} bytes`);
  check("it downloads with an .xlsx filename",
    xlsx.headers.get("content-disposition").endsWith('.xlsx"'));

  const csv = await (await fetch(`${BASE}/api/offers/${offerId}/export.csv`)).text();
  const rows = csv.replace(/^﻿/, "").trim().split("\r\n");
  const header = rows[0].split(",");
  const body = rows.slice(1).map((r) => r.split(","));
  const pieces = body.reduce((sum, r) => sum + Number(r[header.indexOf("quantity")]), 0);
  check(
    "CSV rows == included lines on screen",
    body.length === afterRetry.summary.included_lines,
    `${body.length} rows / ${afterRetry.summary.included_lines} included`,
  );
  check("CSV pieces == summary pieces", pieces === afterRetry.summary.pieces,
    `${pieces} / ${afterRetry.summary.pieces}`);

  step("10. The 5,000-row sheet in the browser");
  const bigPage = await context.newPage();
  await bigPage.goto(`${BASE}/`);
  await bigPage.setInputFiles(
    'input[type="file"]',
    path.join(here, "../backend/tests/fixtures/03-northstar-5000-rows.xlsx"),
  );
  await bigPage.waitForURL(/\/offers\/.+/, { timeout: 60000 });
  await bigPage.waitForSelector(".total-cost .total-figure");
  const bigId = bigPage.url().split("/offers/")[1];
  const big = await api(`/api/offers/${bigId}`);
  await bigPage.getByRole("tab", { name: /All lines/ }).click();
  await bigPage.waitForTimeout(400);
  const rendered = await bigPage.locator(".line").count();
  check(
    "All-lines list is virtualized",
    rendered < 100 && big.summary.total_lines === 5000,
    `${rendered} rows in the DOM for ${big.summary.total_lines} lines`,
  );
  const bigCost = await bigPage.locator(".total-cost .total-figure").innerText();
  check(
    "5,000-row totals match the API",
    bigCost.replace(/[$,]/g, "") === big.summary.supplier_cost,
    bigCost,
  );
  await checkSummaryReconciles(bigPage, big, "5,000-row file");

  step("10a. The Harbor size grid: the summary reconciles there too");
  const harborPage = await context.newPage();
  await harborPage.goto(`${BASE}/`);
  await harborPage.setInputFiles(
    'input[type="file"]',
    path.join(here, "../backend/tests/fixtures/02-harbor-size-grid.xlsx"),
  );
  await harborPage.waitForURL(/\/offers\/.+/, { timeout: 15000 });
  await harborPage.waitForSelector(".overview");
  const harbor = await api(`/api/offers/${harborPage.url().split("/offers/")[1]}`);
  await checkSummaryReconciles(harborPage, harbor, "Harbor");
  await harborPage.close();

  step("11. A rejected value comes back for editing, not lost");
  await page.getByRole("tab", { name: /All lines/ }).click();
  const editable = afterRetry.lines.find((l) => l.status === "included");
  await page.locator(".line-head", { hasText: editable.item_code }).first().click();
  const editRow = page.locator(".line", { hasText: editable.item_code }).first();
  await editRow.locator('label:has-text("Supplier cost") input').fill("abc");
  await editRow.getByRole("button", { name: "Include", exact: true }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.waitForSelector(".line.has-error", { timeout: 15000 });
  const fieldError = await page.locator(".field-errors").first().innerText();
  check("the server's own words are shown on the line", /amount/i.test(fieldError), fieldError.trim());
  check(
    "the rejected edit is still staged, not discarded",
    (await page.locator(".savebar-message").innerText()).includes("1 unsaved"),
  );
  const afterReject = await api(`/api/offers/${offerId}`);
  check("nothing was saved and the version held", afterReject.version === afterRetry.version,
    `v${afterReject.version}`);
  await editRow.locator('label:has-text("Supplier cost") input').fill(editable.unit_cost);
  await editRow.getByRole("button", { name: "Include", exact: true }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.waitForSelector(".savebar.saved", { timeout: 15000 });
  check("a corrected retry saves under a fresh request id",
    (await api(`/api/offers/${offerId}`)).version === afterRetry.version + 1);

  step("12. Two windows: 409, reload, and confirm before overwriting");
  const setup = await page.context().newPage();
  await setup.goto(`${BASE}/`);
  await setup.setInputFiles('input[type="file"]', FIXTURE);
  await setup.waitForURL(/\/offers\/.+/, { timeout: 15000 });
  const sharedId = setup.url().split("/offers/")[1];
  const shared = await api(`/api/offers/${sharedId}`);
  const contested = shared.lines.find((l) =>
    l.issues.some((i) => i.code === "MISSING_COST"),
  );

  const windowB = await browser.newContext();
  const pageB = await windowB.newPage();
  await pageB.goto(`${BASE}/offers/${sharedId}`);
  await pageB.waitForSelector(".total-cost .total-figure");

  // Window A decides the contested line first.
  await setup.locator(".line-head", { hasText: contested.item_code }).first().click();
  const rowA = setup.locator(".line", { hasText: contested.item_code }).first();
  await rowA.locator('label:has-text("Supplier cost") input').fill("9.00");
  await rowA.getByRole("button", { name: "Include", exact: true }).click();
  await setup.getByRole("button", { name: "Save", exact: true }).click();
  await setup.waitForSelector(".savebar.saved", { timeout: 15000 });

  // Window B, still on the old version, edits the same line and saves.
  await pageB.locator(".line-head", { hasText: contested.item_code }).first().click();
  const rowB = pageB.locator(".line", { hasText: contested.item_code }).first();
  await rowB.locator('label:has-text("Supplier cost") input').fill("7.00");
  await rowB.getByRole("button", { name: "Include", exact: true }).click();
  await pageB.getByRole("button", { name: "Save", exact: true }).click();

  await pageB.waitForSelector(".savebar.conflict", { timeout: 15000 });
  const conflictText = await pageB.locator(".savebar-message").innerText();
  check("window B is told another window changed it", conflictText.includes("another window"),
    conflictText);

  await pageB.getByRole("button", { name: "Reload latest" }).click();
  await pageB.waitForSelector(".reapply", { timeout: 15000 });
  const reapplyText = await pageB.locator(".reapply").innerText();
  check(
    "the line the other window touched is named before overwriting",
    reapplyText.includes(contested.line_id),
    reapplyText.split("\n").slice(0, 2).join(" ").trim(),
  );
  check("overwriting needs an explicit choice",
    await pageB.getByRole("button", { name: /Overwrite all/ }).isVisible());

  await pageB.getByRole("button", { name: /leave those alone/ }).click();
  const afterLeave = await api(`/api/offers/${sharedId}`);
  check(
    "declining leaves the other window's decision intact",
    afterLeave.lines.find((l) => l.line_id === contested.line_id).decision.unit_cost === "9.00",
    `cost is ${afterLeave.lines.find((l) => l.line_id === contested.line_id).decision.unit_cost}`,
  );
  await windowB.close();

  step("13. Exact duplicates ask a different question from conflicts");
  const freshOffer = async () => {
    const tab = await context.newPage();
    await tab.goto(`${BASE}/`);
    await tab.setInputFiles('input[type="file"]', FIXTURE);
    await tab.waitForURL(/\/offers\/.+/, { timeout: 15000 });
    // count() does not auto-wait the way innerText() does, so settle first.
    await tab.waitForSelector(".group", { timeout: 15000 });
    const id = tab.url().split("/offers/")[1];
    return { tab, id, data: await api(`/api/offers/${id}`) };
  };

  const dupA = await freshOffer();
  const dupLine = dupA.data.lines.find((l) =>
    l.issues.some((i) => i.code === "DUPLICATE_ROW"),
  );
  const keptLine = dupA.data.lines.find((l) => l.line_id === dupLine.related_line_ids[0]);
  const dupRows = [keptLine.source_row, dupLine.source_row].sort((a, b) => a - b);

  const dupCard = dupA.tab.locator(".group").filter({ hasText: "are identical" });
  check("duplicates get their own heading", (await dupCard.count()) === 1,
    (await dupCard.locator("h3").innerText()));
  check(
    "the heading names both rows",
    (await dupCard.locator("h3").innerText()) === `Rows ${dupRows.join(" and ")} are identical`,
  );
  check("no 'Keep this one' on a duplicate card",
    (await dupCard.getByRole("button", { name: /Keep this one/ }).count()) === 0);
  check("no leftover 'Counted once here' subtitle",
    !(await dupCard.innerText()).includes("Counted once here"),
    (await dupCard.innerText()).split("\n").slice(0, 2).join(" / "));
  check("offers 'Count once'",
    await dupCard.getByRole("button", { name: /Count once/ }).isVisible());
  check("offers 'Count both'",
    await dupCard.getByRole("button", { name: /Count both/ }).isVisible());
  const bothLabel = await dupCard.getByRole("button", { name: /Count both/ }).innerText();
  check("'Count both' says why you would", /separate lots/i.test(bothLabel), bothLabel);

  step("13a. Count once");
  await dupCard.getByRole("button", { name: /Count once/ }).click();
  await dupA.tab.getByRole("button", { name: "Save", exact: true }).click();
  await dupA.tab.waitForSelector(".savebar.saved", { timeout: 15000 });
  const onceResult = await api(`/api/offers/${dupA.id}`);
  const onceKept = onceResult.lines.find((l) => l.line_id === keptLine.line_id);
  const onceDropped = onceResult.lines.find((l) => l.line_id === dupLine.line_id);
  check("counting once keeps the first row and drops the copy",
    onceKept.status === "included" && onceDropped.status === "excluded",
    `${onceKept.line_id}:${onceKept.status} ${onceDropped.line_id}:${onceDropped.status}`);
  check("counting once leaves the totals at the default",
    onceResult.summary.pieces === dupA.data.summary.pieces,
    `${onceResult.summary.pieces} pieces`);
  check("the open question is closed",
    onceResult.lines.filter((l) => [keptLine.line_id, dupLine.line_id].includes(l.line_id) &&
      l.needs_decision_open).length === 0);
  await dupA.tab.close();

  step("13b. Count both — supplier has separate lots");
  const dupB = await freshOffer();
  const cardB = dupB.tab.locator(".group").filter({ hasText: "are identical" });
  await cardB.getByRole("button", { name: /Count both/ }).click();
  await dupB.tab.getByRole("button", { name: "Save", exact: true }).click();
  await dupB.tab.waitForSelector(".savebar.saved", { timeout: 15000 });
  const bothResult = await api(`/api/offers/${dupB.id}`);
  const bothRows = [keptLine.line_id, dupLine.line_id].map((id) =>
    bothResult.lines.find((l) => l.line_id === id));
  check("counting both includes both rows",
    bothRows.every((l) => l.status === "included"),
    bothRows.map((l) => `${l.line_id}:${l.status}`).join(" "));
  check(
    "counting both adds the copy's pieces",
    bothResult.summary.pieces === dupB.data.summary.pieces + dupLine.quantity,
    `${dupB.data.summary.pieces} + ${dupLine.quantity} = ${bothResult.summary.pieces}`,
  );
  const dupWarn = bothRows.every((l) =>
    l.issues.some((i) => i.code === "DUPLICATE_AFTER_EDIT"));
  check("both rows are flagged as possibly double-counted", dupWarn);
  await dupB.tab.close();

  step("14. Every decision made: the page says it is ready to export");
  const all = await freshOffer();
  await shotFull(all.tab, "ui-2-fresh-offer");
  check("export is not highlighted while decisions are open",
    !(await all.tab.getByRole("button", { name: "Export Excel" })
      .evaluate((el) => el.classList.contains("export-ready"))));

  // A flagged value nobody changed is asked for too, not staged: -12 pieces.
  const negative = all.data.lines.find((l) =>
    l.issues.some((i) => i.code === "NEGATIVE_QUANTITY"));
  if (negative) {
    await all.tab.locator(".line-head", { hasText: negative.description }).first().click();
    const negRow = all.tab.locator(".line", { hasText: negative.description }).first();
    await negRow.getByRole("button", { name: "Include", exact: true }).click();
    const negAsk = await negRow.locator(".ask").innerText();
    check("Include on a flagged quantity asks for pieces, naming the sheet's value",
      negAsk.includes("number of pieces") && negAsk.includes(String(negative.quantity)), negAsk);
    check("…and stages nothing",
      (await all.tab.locator(".savebar-message").innerText()) === "No unsaved changes");
    await negRow.locator(".line-head").click();
  }

  // Groups by their own buttons; every other open line is excluded, so no
  // value is typed that a person did not supply.
  for (const card of await all.tab.locator(".group").all()) {
    if ((await card.locator("h3").innerText()).includes("are identical")) {
      await card.getByRole("button", { name: /Count once/ }).click();
    } else {
      await card.getByRole("button", { name: "Keep this one" }).first().click();
    }
  }
  for (const line of all.data.lines.filter((l) =>
    l.needs_decision_open && l.related_line_ids.length === 0)) {
    await all.tab.locator(".line-head", { hasText: line.description }).first().click();
    const row = all.tab.locator(".line", { hasText: line.description }).first();
    await row.getByRole("button", { name: "Exclude", exact: true }).click();
    await row.locator(".line-head").click();
  }
  const stagedAll = await all.tab.locator(".decisions-count").innerText();
  check("chosen-but-unsaved decisions are shown as such",
    stagedAll.includes("not saved yet"), stagedAll.replace(/\s+/g, " "));
  await all.tab.getByRole("button", { name: "Save", exact: true }).click();
  await all.tab.waitForSelector(".savebar.saved", { timeout: 15000 });

  const allDone = await api(`/api/offers/${all.id}`);
  check("the server agrees nothing is open", allDone.summary.needs_decision_open === 0);
  const done = await all.tab.locator(".decisions-done").innerText();
  check("'All decisions made — ready to export'",
    done.includes("All decisions made — ready to export"), done);
  check("the export button is highlighted",
    await all.tab.getByRole("button", { name: "Export Excel" })
      .evaluate((el) => el.classList.contains("export-ready")));
  check("no review button once nothing is open",
    (await all.tab.getByRole("button", { name: /^Review \d+ decision/ }).count()) === 0);
  const leftOutChip = count(await all.tab.locator('.chip[data-kind="left-out"] > b').innerText());
  const readyChip = count(await all.tab.locator('.chip[data-kind="ready"] > b').innerText());
  check("summary chips still match the API",
    readyChip === allDone.summary.included_lines &&
      leftOutChip === leftOut(allDone).length &&
      readyChip + leftOutChip === allDone.summary.total_lines,
    `${readyChip} ready + ${leftOutChip} left out of ${allDone.summary.total_lines}`);
  await checkSummaryReconciles(all.tab, allDone, "Northstar, all decided");
  await all.tab.getByRole("tab", { name: /All lines/ }).click();
  await shotFull(all.tab, "ui-3-all-decided");
  await all.tab.close();

} catch (error) {
  bad("script completed", String(error).split("\n")[0]);
} finally {
  await browser.close();
  console.log(`\n${failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`}`);
  process.exit(failures === 0 ? 0 : 1);
}
