// Renders favicons and app icons from the brand SVGs in docs/brand/.
//
//   docs/brand/wieplay-icon-{vb,bb}.svg  →  docs/brand/icons/{vb,bb}/
//     favicon.svg, favicon.ico (16/32/48): the ball alone, which stays readable in a tab
//     icon-192.png, icon-512.png, icon-maskable-192.png, icon-maskable-512.png,
//     apple-touch-icon.png (180): the full icon
//   docs/brand/wieplay-mark-{vb,bb}.svg  →  docs/brand/icons/{vb,bb}/mark-on-dark.svg
//   docs/brand/icons/site.webmanifest  (default variant: vb)
//
// Rasterises with Playwright's headless Chromium. See scripts/brand/README.md.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const { chromium } = await import(
  process.env.PLAYWRIGHT_CORE ? pathToFileURL(process.env.PLAYWRIGHT_CORE).href : 'playwright-core'
);

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const brand = path.join(root, 'docs/brand');
const SPORTS = ['vb', 'bb'];
const NAVY = '#13203A';
const LIGHT = '#EEF1F5';

// Maskable and Apple touch icons must fill the whole square: drop the rounded corners.
// The artwork already sits inside the maskable safe zone (central circle, 80 % diameter).
const fullBleed = (svg) => svg.replace(/(<rect\b[^>]*?)\s+rx="[^"]*"/, '$1');

// The mark's trail dots are navy and vanish on a navy background; light dots for dark UI.
const markOnDark = (svg) => svg.replace(`<g fill="${NAVY}">`, `<g fill="${LIGHT}">`);

// At 16–48 px the trail of dots is a few pixels wide, so the favicon is the ball alone,
// taken from the icon (radius 10 plus its outline) and centred on a transparent square.
function ballOnly(iconSvg, sport) {
  // The ball is the group whose first child is the radius-10 circle (the outer group also
  // has a translate/scale transform, so match on the circle, not on the transform alone).
  const ball = iconSvg.match(/<g transform="translate\([^)]*\) scale\([^)]*\)">\s*(<circle r="10"[\s\S]*?)<\/g>/);
  if (!ball) throw new Error(`ball group not found in wieplay-icon-${sport}.svg`);
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-11 -11 22 22" width="512" height="512">\n` +
    `  <title>WiePlay – ${sport === 'bb' ? 'basketball' : 'volleyball'} favicon</title>\n` +
    `  ${ball[1].trimEnd()}\n</svg>\n`
  );
}

async function render(page, svg, size) {
  await page.setViewportSize({ width: size, height: size });
  const src = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
  await page.setContent(
    `<!doctype html><body style="margin:0;background:transparent">` +
      `<img src="${src}" width="${size}" height="${size}" style="display:block"></body>`,
  );
  await page.locator('img').evaluate((img) => img.decode());
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

// ICO container holding PNG images (supported by every current browser).
function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  const dir = Buffer.alloc(16 * images.length);
  let offset = header.length + dir.length;
  images.forEach(({ size, data }, i) => {
    const o = i * 16;
    dir.writeUInt8(size % 256, o);
    dir.writeUInt8(size % 256, o + 1);
    dir.writeUInt16LE(1, o + 4);
    dir.writeUInt16LE(32, o + 6);
    dir.writeUInt32LE(data.length, o + 8);
    dir.writeUInt32LE(offset, o + 12);
    offset += data.length;
  });
  return Buffer.concat([header, dir, ...images.map((img) => img.data)]);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const sport of SPORTS) {
    const icon = await readFile(path.join(brand, `wieplay-icon-${sport}.svg`), 'utf8');
    const mark = await readFile(path.join(brand, `wieplay-mark-${sport}.svg`), 'utf8');
    const out = path.join(brand, 'icons', sport);
    await mkdir(out, { recursive: true });

    const favicon = ballOnly(icon, sport);
    await writeFile(path.join(out, 'favicon.svg'), favicon);
    const small = [];
    for (const size of [16, 32, 48]) small.push({ size, data: await render(page, favicon, size) });
    await writeFile(path.join(out, 'favicon.ico'), ico(small));
    for (const size of [192, 512]) {
      await writeFile(path.join(out, `icon-${size}.png`), await render(page, icon, size));
      await writeFile(path.join(out, `icon-maskable-${size}.png`), await render(page, fullBleed(icon), size));
    }
    await writeFile(path.join(out, 'apple-touch-icon.png'), await render(page, fullBleed(icon), 180));
    await writeFile(path.join(out, 'mark-on-dark.svg'), markOnDark(mark));
  }
} finally {
  await browser.close();
}

const manifest = {
  name: 'WiePlay',
  short_name: 'WiePlay',
  start_url: '/',
  display: 'standalone',
  theme_color: NAVY,
  background_color: LIGHT,
  icons: [
    { src: '/icons/vb/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
    { src: '/icons/vb/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
    { src: '/icons/vb/icon-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
    { src: '/icons/vb/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
  ],
};
await writeFile(path.join(brand, 'icons', 'site.webmanifest'), JSON.stringify(manifest, null, 2) + '\n');
console.log('icons written to docs/brand/icons/');
