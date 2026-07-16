// Navigate to a screen via menu > item and screenshot it. Signs in as admin.
// usage: node shot_screen.mjs "<Menu>" "<Item>" /tmp/out.png "<expected text>"
import puppeteer from "puppeteer-core";

const [menu, item, out, expect] = process.argv.slice(2);
const API = "http://localhost:8010";
const lr = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    email: process.env.SENTIGON_ADMIN_EMAIL ?? "admin@sentigon.local",
    password: process.env.SENTIGON_ADMIN_PASSWORD ?? "",
  }),
});
const auth = await lr.json();
const stored = JSON.stringify({ token: auth.access_token, user: { email: auth.email, name: auth.name, role: auth.role } });

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--autoplay-policy=no-user-gesture-required", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--window-size=1680,1000"],
});
const errors = [];
try {
  const page = await browser.newPage();
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto("http://localhost:3001", { waitUntil: "networkidle2", timeout: 30000 });
  await page.evaluate((s) => localStorage.setItem("sentigon_auth", s), stored);
  await page.reload({ waitUntil: "networkidle2" });
  await new Promise((r) => setTimeout(r, 1200));
  await page.evaluate((m) => {
    const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent?.trim() === m);
    b?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  }, menu);
  await new Promise((r) => setTimeout(r, 400));
  await page.evaluate((it) => {
    const b = Array.from(document.querySelectorAll("button")).find((x) => x.textContent?.trim() === it);
    b?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  }, item);
  await new Promise((r) => setTimeout(r, 5000));
  const found = expect ? await page.evaluate((t) => document.body.innerText.includes(t), expect) : true;
  console.log(`expected text "${expect}": ${found}`);
  console.log(`page errors: ${errors.length ? errors.slice(0, 2).join(" | ") : "none"}`);
  await page.screenshot({ path: out });
  console.log(`screenshot: ${out}`);
} finally {
  await browser.close();
}
