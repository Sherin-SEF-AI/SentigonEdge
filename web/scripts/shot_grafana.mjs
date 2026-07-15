import puppeteer from "puppeteer-core";

const url =
  "http://localhost:3002/d/sentigon-overview/sentigon-overview?kiosk&from=now-30m&to=now&refresh=5s";
const out = "/tmp/sentigon-grafana-loki.png";

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1680,1050"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1050 });
  await page.goto(url, { waitUntil: "networkidle2", timeout: 40000 });
  await new Promise((r) => setTimeout(r, 16000)); // let panels query + render
  const hasLogs = await page.evaluate(() => document.body.innerText.includes("Loki"));
  console.log(`loki panels present: ${hasLogs}`);
  await page.screenshot({ path: out, fullPage: false });
  console.log(`screenshot: ${out}`);
} finally {
  await browser.close();
}
