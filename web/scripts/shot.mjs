// Headless screenshot of the console, used to prove real WebRTC playback.
// Reports each <video> element's decoded dimensions and currentTime so we can
// assert frames are actually flowing, not just that the DOM rendered.
import puppeteer from "puppeteer-core";

const url = process.argv[2] || "http://localhost:3001";
const out = process.argv[3] || "/tmp/sentigon-wall.png";
const waitMs = Number(process.argv[4] || 9000);
const clickTitle = process.argv[5] || "";

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--autoplay-policy=no-user-gesture-required",
    "--enable-unsafe-swiftshader",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--window-size=1680,1000",
  ],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(url, { waitUntil: "networkidle2", timeout: 30000 });
  await new Promise((r) => setTimeout(r, waitMs));

  if (clickTitle) {
    await page.evaluate((t) => {
      const btn = document.querySelector(`button[title="${t}"]`);
      if (btn) btn.click();
    }, clickTitle);
    await new Promise((r) => setTimeout(r, 3500));
  }

  const videos = await page.evaluate(() =>
    Array.from(document.querySelectorAll("video")).map((v) => ({
      w: v.videoWidth,
      h: v.videoHeight,
      t: Number(v.currentTime.toFixed(2)),
      ready: v.readyState,
    })),
  );
  const playing = videos.filter((v) => v.w > 0 && v.t > 0).length;
  console.log(`videos: ${JSON.stringify(videos)}`);
  console.log(`playing (decoded frames + advancing time): ${playing}/${videos.length}`);

  await page.screenshot({ path: out });
  console.log(`screenshot: ${out}`);
} finally {
  await browser.close();
}
