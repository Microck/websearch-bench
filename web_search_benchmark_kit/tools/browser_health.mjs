#!/usr/bin/env node

/*
 * Check URL navigation with a real Chromium page.
 * The checker records document responses only. It never stores page bodies,
 * cookies, or browser state.
 */

import fs from "node:fs/promises";
import { dirname } from "node:path";
import { chromium } from "playwright";

function argument(name, fallback = "") {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] ?? fallback : fallback;
}

function csvField(value) {
  const text = String(value ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function parseOneColumnCsv(text) {
  const lines = text.trimEnd().split(/\r?\n/);
  if (!lines.length || lines[0].replace(/^"|"$/g, "") !== "url") {
    throw new Error("input CSV must have a url header");
  }
  return lines.slice(1).filter(Boolean).map((line) => {
    if (line.startsWith('"') && line.endsWith('"')) {
      return line.slice(1, -1).replaceAll('""', '"');
    }
    return line;
  });
}

async function checkUrl(page, url, timeout, settleMs, ignoreHTTPSErrors) {
  const documentResponses = [];
  const onResponse = (response) => {
    if (response.request().resourceType() === "document") {
      documentResponses.push({ status: response.status(), url: response.url() });
    }
  };
  page.on("response", onResponse);
  let error = "";
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout });
    if (settleMs > 0) await page.waitForTimeout(settleMs);
  } catch (cause) {
    error = String(cause?.message ?? cause).slice(0, 500);
  }
  let title = "";
  try {
    title = (await page.title()).slice(0, 500);
  } catch {
    // A navigation error may leave no document to query.
  }
  const finalUrl = page.url();
  page.off("response", onResponse);
  const lastResponse = documentResponses.at(-1);
  return {
    original_url: url,
    final_url: finalUrl === "about:blank" ? "" : finalUrl,
    http_status: lastResponse?.status ?? "",
    initial_http_status: documentResponses[0]?.status ?? "",
    document_responses: documentResponses.length,
    error,
    page_title: title,
    https_errors: ignoreHTTPSErrors ? "ignored" : "verified",
  };
}

async function main() {
  const input = argument("input");
  const output = argument("out");
  const workers = Math.max(1, Number(argument("workers", "6")));
  const timeout = Math.max(1000, Number(argument("timeout", "15000")));
  const settleMs = Math.max(0, Number(argument("settle-ms", "1500")));
  const ignoreHTTPSErrors = argument("ignore-https-errors", "false") === "true";
  const headed = argument("headed", "false") === "true";
  if (!input || !output) throw new Error("--input and --out are required");

  const urls = parseOneColumnCsv(await fs.readFile(input, "utf8"));
  const browser = await chromium.launch({
    headless: !headed,
    executablePath: "/usr/bin/chromium-browser",
    args: ["--disable-dev-shm-usage"],
  });
  const rows = [];
  let nextIndex = 0;
  async function worker(workerIndex) {
    const context = await browser.newContext({
      ignoreHTTPSErrors,
      locale: "en-US",
      viewport: { width: 1365, height: 900 },
      extraHTTPHeaders: {
        "Accept-Language": "en-US,en;q=0.9",
      },
    });
    while (true) {
      const index = nextIndex++;
      if (index >= urls.length) break;
      const page = await context.newPage();
      try {
        rows[index] = await checkUrl(page, urls[index], timeout, settleMs, ignoreHTTPSErrors);
      } finally {
        await page.close();
      }
      if ((index + 1) % 25 === 0 || index + 1 === urls.length) {
        console.log(`browser worker ${workerIndex}: ${index + 1}/${urls.length}`);
      }
    }
    await context.close();
  }
  await Promise.all(Array.from({ length: workers }, (_, index) => worker(index + 1)));
  await browser.close();

  const columns = [
    "original_url",
    "final_url",
    "http_status",
    "initial_http_status",
    "document_responses",
    "error",
    "page_title",
    "https_errors",
  ];
  const outputText = [
    columns.join(","),
    ...rows.map((row) => columns.map((column) => csvField(row[column])).join(",")),
    "",
  ].join("\n");
  await fs.mkdir(dirname(output), { recursive: true });
  await fs.writeFile(output, outputText, "utf8");
  console.log(`wrote ${rows.length} browser URL resolutions to ${output}`);
}

main().catch((error) => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});
