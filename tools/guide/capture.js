// Capture report sections as PNG figures for the user guide.
// Usage: node tools/guide/capture.js <figure folder>
// Each entry clips from a heading to the next heading (or to a named end heading).
const { chromium } = require('playwright');
const path = require('path');

const SHOTS = [
  ['design_report.html', 'Demo Road', 'fig_report_top', 900, 'Plan'],
  ['design_report.html', 'Plan', 'fig_report_plan', 900],
  ['design_report.html', 'Curvature diagram', 'fig_report_curvature', 600],
  ['design_report.html', 'Gradient diagram', 'fig_report_gradient', 600],
  ['design_report.html', 'Superelevation diagram', 'fig_report_superelevation', 600],
  ['review.html', 'Geometric design review —', 'fig_review_summary', 700, 'Design speed sections'],
  ['review.html', 'Design speed sections', 'fig_review_speed', 400],
  ['review.html', 'Phasing diagram', 'fig_review_phasing', 500],
  ['review.html', 'Recommended profile', 'fig_review_delta', 500],
  ['review.html', 'Recommended PVIs', 'fig_review_pvis', 700],
  ['review.html', 'Safety barrier schedule', 'fig_review_barriers', 600],
  ['review.html', 'Findings and recommendations', 'fig_review_findings', 760],
  ['profile_cut_fill.html', 'Demo Road', 'fig_cutfill_report', 1000, 'Preliminary level-section estimate'],
  ['corridor_quantities.html', 'Demo Road', 'fig_quantities_top', 900, 'Mass haul ordinate'],
  ['corridor_quantities.html', 'Mass haul ordinate', 'fig_masshaul', 600],
];

(async () => {
  const folder = path.resolve(process.argv[2]);
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' }).catch(() => chromium.launch());
  const page = await browser.newPage({ viewport: { width: 1100, height: 1400 }, deviceScaleFactor: 2 });
  for (const [file, heading, name, maxHeight, until] of SHOTS) {
    await page.goto('file://' + path.join(folder, file));
    const box = await page.evaluate(([text, limit, end]) => {
      const heads = [...document.querySelectorAll('h1,h2,h3')];
      const index = heads.findIndex((h) => h.textContent.trim().startsWith(text));
      if (index < 0) return null;
      const head = heads[index];
      const level = Number(head.tagName[1]);
      const next = end
        ? heads.slice(index + 1).find((h) => h.textContent.trim().startsWith(end))
        : heads.slice(index + 1).find((h) => Number(h.tagName[1]) <= Math.max(level, 3));
      const top = head.getBoundingClientRect().top + window.scrollY - 6;
      const bottom = next ? next.getBoundingClientRect().top + window.scrollY - 8 : document.body.scrollHeight;
      const body = document.body.getBoundingClientRect();
      return { x: Math.max(body.left, 0), y: top, width: Math.min(body.width, 1100), height: Math.min(bottom - top, limit) };
    }, [heading, maxHeight, until]);
    if (!box) { console.log('missing', file, heading); continue; }
    await page.screenshot({ path: path.join(folder, name + '.png'), clip: box, fullPage: true });
    console.log('saved', name, Math.round(box.height));
  }
  await browser.close();
})();
