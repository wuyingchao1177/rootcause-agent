#!/usr/bin/env node
/* v3: headless Chrome + 拖放导入渲染 Excalidraw → PNG */
const path = require("path");
const fs = require("fs");
const puppeteer = require("/opt/homebrew/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer");

const CHROME = process.env.HOME +
  "/.cache/puppeteer/chrome-headless-shell/mac_arm-151.0.7922.71/chrome-headless-shell-mac-arm64/chrome-headless-shell";
const SRC = process.argv[3] || "docs/competition-diagrams";
const OUT = process.argv[2] || "docs/competition-diagrams/png";

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const files = fs.readdirSync(SRC).filter(f => f.endsWith(".excalidraw"));
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-sandbox", "--disable-gpu", "--force-device-scale-factor=2"],
    defaultViewport: { width: 2200, height: 1500 },
  });

  for (const f of files) {
    const page = await browser.newPage();
    try {
      await page.goto("https://excalidraw.com/", { waitUntil: "networkidle2", timeout: 90000 });
      await page.waitForSelector("canvas", { timeout: 60000 });
      await new Promise(r => setTimeout(r, 4000));
      // 清空场景缓存（避免读到上一张图的 localStorage 残留）
      await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
      await page.reload({ waitUntil: "networkidle2" });
      await page.waitForSelector("canvas", { timeout: 60000 });
      await new Promise(r => setTimeout(r, 5000));
      // 拖放导入 + 场景变化验证（失败重试，最多 4 次）
      const filePath = path.join(SRC, f);
      const content = fs.readFileSync(filePath, "utf-8");
      let loaded = false;
      for (let attempt = 1; attempt <= 4 && !loaded; attempt++) {
        const before = await page.evaluate(() => {
          const c = document.querySelector("canvas");
          return c ? c.toDataURL().slice(0, 80) : "no-canvas";
        });
        await page.evaluate((fileContent, fileName) => {
          const dt = new DataTransfer();
          dt.items.add(new File([fileContent], fileName, { type: "application/json" }));
          const canvas = document.querySelector("canvas");
          const targets = [canvas, document.body];
          for (const t of targets) {
            t.dispatchEvent(new DragEvent("dragenter", { dataTransfer: dt, bubbles: true }));
            t.dispatchEvent(new DragEvent("dragover", { dataTransfer: dt, bubbles: true }));
            t.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true }));
          }
        }, content, f);
        await new Promise(r => setTimeout(r, 8000));
        const after = await page.evaluate(() => {
          const c = document.querySelector("canvas");
          return c ? c.toDataURL().slice(0, 80) : "no-canvas";
        });
        if (before !== after) { loaded = true; console.log(`[${f}] 场景已加载 (attempt ${attempt})`); }
        else console.log(`[${f}] drop 未生效，重试 ${attempt}`);
      }
      if (!loaded) { console.error(`❌ ${f}: drop 反复失败`); continue; }
      try {
        await page.keyboard.down("Control");
        await page.keyboard.press("Shift");
        await page.keyboard.press("Digit1");
        await page.keyboard.up("Control");
        await page.keyboard.up("Shift");
      } catch (e) {}
      await new Promise(r => setTimeout(r, 3000));
      const name = f.replace(/\.excalidraw$/, ".png");
      await page.screenshot({ path: path.join(OUT, name) });
      console.log(`✅ ${name}`);
    } catch (e) {
      console.error(`❌ ${f}: ${e.message.slice(0, 140)}`);
    } finally {
      await page.close();
    }
  }
  await browser.close();
  console.log("完成");
})().catch(e => { console.error("FATAL:", e.message); process.exit(1); });
