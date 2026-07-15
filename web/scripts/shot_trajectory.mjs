import puppeteer from "puppeteer-core";

const url = "http://localhost:3001";
const out = "/tmp/sentigon-trajectory.png";

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1680,1000"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
  await new Promise((r) => setTimeout(r, 1200));

  // Investigate menu -> Entity Trajectories
  await page.evaluate(() => {
    const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent?.trim() === "Investigate");
    b?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 400));
  await page.evaluate(() => {
    const it = Array.from(document.querySelectorAll("button")).find((x) => x.textContent?.trim() === "Entity Trajectories");
    it?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 2500)); // tracks load

  // click the first track in the picker
  await page.evaluate(() => {
    const btn = Array.from(document.querySelectorAll("button")).find((x) => /track#\d+/.test(x.textContent || ""));
    btn?.click();
  });
  await new Promise((r) => setTimeout(r, 3500)); // trajectory query

  const hasPath = await page.evaluate(() => document.body.innerText.includes("Cross-camera path"));
  console.log(`trajectory rendered: ${hasPath}`);
  await page.screenshot({ path: out });
  console.log(`screenshot: ${out}`);
} finally {
  await browser.close();
}
