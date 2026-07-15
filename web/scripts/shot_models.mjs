// Screenshot the Model Governance dashboard, signed in as admin.
// Injects a real JWT (fetched from /auth/login) into localStorage so the
// authorized view (promote buttons enabled) renders, then navigates via the menu.
import puppeteer from "puppeteer-core";

const API = "http://localhost:8010";
const url = "http://localhost:3001";
const out = "/tmp/sentigon-models.png";

const lr = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "admin@sentigon.local", password: "changeme123" }),
});
const auth = await lr.json();
const stored = JSON.stringify({
  token: auth.access_token,
  user: { email: auth.email, name: auth.name, role: auth.role },
});

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1680,1000"],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
  await page.evaluate((s) => localStorage.setItem("sentigon_auth", s), stored);
  await page.reload({ waitUntil: "networkidle2" });
  await new Promise((r) => setTimeout(r, 1500));

  // Analytics menu -> Model Governance
  await page.evaluate(() => {
    const btn = Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.trim() === "Analytics");
    btn?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 400));
  await page.evaluate(() => {
    const item = Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.trim() === "Model Governance");
    item?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  await new Promise((r) => setTimeout(r, 3500));
  // expand the first per-class <details> so the class breakdown is visible
  await page.evaluate(() => {
    const d = document.querySelector("details");
    if (d) d.open = true;
  });
  await new Promise((r) => setTimeout(r, 600));

  const cards = await page.evaluate(() => document.body.innerText.includes("Champion"));
  const signedIn = await page.evaluate(() => document.body.innerText.includes("admin@sentigon.local"));
  console.log(`signed in as admin: ${signedIn}`);
  console.log(`governance view rendered: ${cards}`);
  await page.screenshot({ path: out });
  console.log(`screenshot: ${out}`);
} finally {
  await browser.close();
}
