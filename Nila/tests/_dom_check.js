// Executes the real dashboard page against a real server and reports whether
// every panel left its loading state. Run by tests/test_dashboard_dom.py.
const { JSDOM } = require(process.env.JSDOM_PATH || '/tmp/jsd/node_modules/jsdom');

(async () => {
  const base = process.argv[2], token = process.argv[3];
  const html = await (await fetch(base + '/')).text();
  const errors = [];
  const dom = new JSDOM(html, {
    url: base + '/#' + token, runScripts: 'dangerously', resources: 'usable',
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = (p, o) => fetch(base + p, o);
      win.matchMedia = () => ({ matches: false, addEventListener() {} });
      win.requestAnimationFrame = f => setTimeout(() => f(1), 60);
      win.alert = m => errors.push('alert: ' + m);
      win.addEventListener('error', e => errors.push(String(e.error || e.message)));
    },
  });
  await new Promise(r => setTimeout(r, 3500));
  const doc = dom.window.document;
  const stuck = [];
  doc.querySelectorAll('.skel').forEach(s => {
    let p = s.parentElement, path = [];
    while (p && p.tagName !== 'BODY') { path.unshift(p.id || p.className || p.tagName); p = p.parentElement; }
    stuck.push(path.slice(-1)[0] + ': ' + s.textContent.trim().slice(0, 40));
  });
  const out = {
    errors, stuck,
    panels_ok: doc.querySelectorAll('#st .m').length > 0,
    week_cells: doc.querySelectorAll('.week .day').length,
    capability_rows: doc.querySelectorAll('#caps .cap').length,
    audit_lines: doc.querySelectorAll('#log div').length,
    log_text: (doc.getElementById('log') || {}).textContent,
  };
  console.log(JSON.stringify(out));
  process.exit(0);
})();
