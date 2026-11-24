// Exercise the shipped viewer functions without a browser or a JavaScript dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../landxml/viewer.html'), 'utf8');
const script = html.split('<script type="text/plain" id="viewer-main">')[1].split('</script>')[0];
const loader = html.split('<script>')[1].split('</script>')[0];
new vm.Script(script);
const units = script.slice(script.indexOf('const unitInfo'), script.indexOf('const canvas'));
function source(name) {
  const match = script.match(new RegExp(`function ${name}\\([^]*?\\n\\}`));
  assert.ok(match, name);
  return match[0];
}
function viewer(horizontal, vertical, station = horizontal) {
  const S = { units: { horizontal, vertical }, station_unit: station,
    extent: { min: [0,0,0], max: [100,100,10] },
    lines: [{ points: [[0,0,10]], lift: 1 }], labels: [{ position: [0,0,10], lift: 1 }],
    paths: [{ points: [[0,0,10]], stations: [1200,1300], station_unit: station }], views: [{ target: [0,0,10] }] };
  return vm.runInNewContext(`${units}\n${source('stationText')}\n${source('parseStation')}\n${source('advanceStation')}
    ({stationText, parseStation, advanceStation, verticalLabel, horizontalMetres, zFactor, scene:S})`, { S });
}
test('foot and survey-foot engineering stations preserve signed source stationing', () => {
  for (const unit of ['foot','USSurveyFoot']) {
    const v = viewer(unit, unit);
    assert.equal(v.parseStation('12+50'),1250);
    assert.equal(v.parseStation('-12+50'),-1250);
    assert.equal(v.stationText(1250),'12+50');
    assert.equal(v.parseStation('12+100'),null);
    assert.equal(v.parseStation('1250junk'),null);
    assert.ok(v.verticalLabel.startsWith('ft'));
  }
  const v = viewer('meter','meter');
  assert.equal(v.parseStation('1+250'),1250);
  assert.equal(v.stationText(1250),'1+250');
});
test('mixed-unit geometry, label lifts and targets use the same physical scale', () => {
  const v = viewer('foot','meter');
  assert.ok(Math.abs(v.scene.extent.max[2] - 32.80839895013123) < 1e-10);
  for (const p of [v.scene.lines[0].points[0],v.scene.labels[0].position,v.scene.paths[0].points[0],v.scene.views[0].target]) {
    assert.ok(Math.abs(p[2] - 32.80839895013123) < 1e-10);
  }
  assert.equal(v.verticalLabel,'m');
});
test('physical driving speed uses source station units even after reprojection', () => {
  for (const unit of ['foot','USSurveyFoot']) {
    const v = viewer('meter','foot',unit);
    const p = { stations: [0,10000], station_unit: unit };
    const actual = v.advanceStation(p,0,13.4112,1); // 30 mph for one second
    assert.ok(Math.abs(actual * (unit === 'foot' ? .3048 : 1200/3937) - 13.4112) < 1e-10);
    assert.equal(v.stationText(1250),'12+50');
  }
});
test('unknown source stations stay numeric and do not acquire CRS units', () => {
  const v = viewer('foot','foot',null);
  assert.equal(v.parseStation('12+50'),null);
  assert.equal(v.parseStation('1250'),1250);
  assert.equal(v.stationText(1250),'1250');
  assert.equal(v.advanceStation({stations:[0,10000]},1250,10,1),1250);
});

// ---- drive-through camera modes and the vehicle model
function rig() {
  const vehicles = script.slice(script.indexOf('const VEHICLES'), script.indexOf('const VEHICLE_MAX_VERTS'));
  return vm.runInNewContext(`${vehicles}\n${source('cameraRig')}\n${source('vehicleGeometry')}\n({cameraRig, vehicleGeometry, VEHICLES})`, {});
}
// a straight road running north (+y) at elevation 10; `at(d)` is the road point d metres along
const north = (m = 1, end = Infinity) => d => [0, Math.min(d / m, end), 10];
test('driver camera sits at eye height and looks along the road', () => {
  const { cameraRig } = rig();
  const r = cameraRig('driver', { p: [0, 0, 10], at: north(), ve: 1, m: 1, eyeHeight: 1.3, length: 4.4, progress: 0 });
  assert.ok(r.eye[0] === 0 && r.eye[1] === 0 && Math.abs(r.eye[2] - 11.3) < 1e-9);
  assert.ok(Math.abs(r.target[1] - 30) < 1e-9 && Math.abs(r.target[2] - 11.04) < 1e-9);
  assert.equal(r.vehicle, false);
});
test('chase camera sits behind and above the vehicle and shows it', () => {
  const { cameraRig } = rig();
  const r = cameraRig('chase', { p: [0, 0, 10], at: north(), ve: 1, m: 1, eyeHeight: 1.3, length: 4.4, progress: 0 });
  assert.ok(Math.abs(r.eye[1] + 14.4) < 1e-9, 'behind by 10 m + vehicle length');
  assert.ok(Math.abs(r.eye[2] - 14.5) < 1e-9, 'above by 3.2 m + eye height');
  assert.equal(r.vehicle, true);
  assert.deepEqual(Array.from(r.dir), [0, 1]);
});
test('camera heights follow the horizontal unit and the vertical exaggeration', () => {
  const { cameraRig } = rig();
  const m = 1 / 0.3048, feet = cameraRig('driver', { p: [0, 0, 10], at: north(m), ve: 2, m, eyeHeight: 1.3, length: 4.4, progress: 0 });
  assert.ok(Math.abs(feet.eye[2] - (10 * 2 + 1.3 * m)) < 1e-9);
  const aerial = cameraRig('aerial', { p: [0, 0, 10], at: north(m), ve: 1, m, eyeHeight: 1.3, length: 4.4, progress: 0 });
  assert.ok(Math.abs(aerial.eye[2] - (10 + 38 * m)) < 1e-9);
  assert.ok(Math.abs(aerial.eye[1] + 45 * m) < 1e-9);
});
test('flythrough swings across the road and stays on the road at the ends', () => {
  const { cameraRig } = rig();
  const o = progress => ({ p: [0, 0, 10], at: north(), ve: 1, m: 1, eyeHeight: 1.3, length: 4.4, progress });
  assert.ok(Math.abs(cameraRig('fly', o(0)).eye[0]) < 1e-9);
  assert.ok(Math.abs(cameraRig('fly', o(0.125)).eye[0]) > 49, 'a quarter of the first swing is at the full 50 m');
  // at the end of the road nothing lies ahead: the camera still looks straight on, not at itself
  const end = cameraRig('driver', { p: [0, 0, 10], at: north(1, 0), ve: 1, m: 1, eyeHeight: 1.3, length: 4.4, progress: 1 });
  assert.ok(end.target[1] > 25 && end.eye.every(Number.isFinite) && end.target.every(Number.isFinite));
  // a one-point road has no direction: default north rather than NaN
  const flat = cameraRig('chase', { p: [5, 5, 1], at: () => [5, 5, 1], ve: 1, m: 1, eyeHeight: 1.3, length: 4.4, progress: 0 });
  assert.ok(flat.eye.every(Number.isFinite) && flat.target.every(Number.isFinite));
});
test('vehicle keeps its true size under vertical exaggeration and turns with the road', () => {
  const { vehicleGeometry, VEHICLES } = rig();
  const bounds = g => { const b = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    for (let i = 0; i < g.pos.length; i += 3) for (let k = 0; k < 3; k++) { b[k] = Math.min(b[k], g.pos[i + k]); b[k + 3] = Math.max(b[k + 3], g.pos[i + k]); } return b; };
  for (const type of Object.keys(VEHICLES)) {
    const g = vehicleGeometry(type, [0, 0, 10], [0, 1], 1, 1);
    assert.ok(g.count > 0 && g.count % 3 === 0 && g.pos.length / 3 <= 1000 && g.count <= 4000, 'fits the vertex and index buffers');
    for (const index of g.idx) assert.ok(index < g.pos.length / 3);
    for (let i = 0; i < g.nrm.length; i += 3) assert.ok(Math.abs(Math.hypot(g.nrm[i], g.nrm[i + 1], g.nrm[i + 2]) - 1) < 1e-5);
    const b = bounds(g);
    assert.ok(Math.abs(b[4] - b[1] - VEHICLES[type].length) < 0.02, `${type} is as long as it says`);
    assert.ok(Math.abs(b[2] - 10) < 1e-6, `${type} stands on the road, its wheels on the surface`);
    assert.ok(Math.abs((b[4] + b[1]) / 2) < 0.02, `${type} is centred on the point on the road`);
  }
  const car = bounds(vehicleGeometry('car', [0, 0, 10], [0, 1], 1, 1));
  assert.ok(Math.abs(car[5] - 11.5) < 1e-5 && car[3] - car[0] < 2.0, 'a car is 1.5 m tall and under 2 m wide');
  const flat = bounds(vehicleGeometry('car', [0, 0, 10], [0, 1], 2, 1));
  assert.ok(Math.abs(flat[5] - 10.75) < 1e-5, 'z is halved so the shader scaling by 2 restores 1.5 m');
  const east = bounds(vehicleGeometry('car', [0, 0, 10], [1, 0], 1, 1));
  assert.ok(Math.abs(east[3] - east[0] - 4.5) < 0.02, 'heading east: length runs along x');
  const metric = bounds(vehicleGeometry('car', [0, 0, 10], [0, 1], 1, 1 / 0.3048));
  assert.ok(Math.abs(metric[4] - metric[1] - 4.5 / 0.3048) < 0.05, 'in feet the car is 14.8 ft long');
  const truck = bounds(vehicleGeometry('truck', [0, 0, 10], [0, 1], 1, 1));
  assert.ok(truck[5] - truck[2] > 3.3 && truck[5] - truck[2] < 3.6, 'a truck is a little over 3.4 m tall');
});

// ---- instanced vegetation
test('a grass clump is three crossed quads and a ground patch', () => {
  const { grassTemplate } = vm.runInNewContext(`${source('grassTemplate')}\n({grassTemplate})`, {});
  const t = grassTemplate();
  assert.equal(t.vertices, 24, '8 triangles');
  for (let i = 0; i < t.uv.length; i++) assert.ok(t.uv[i] >= -1 && t.uv[i] <= 1);
  assert.equal(Array.from(t.uv).filter((v, i) => i % 2 === 1 && v < 0).length, 6, 'the patch is marked by v below 0');
  for (let i = 0; i < t.pos.length; i += 3) assert.ok(Math.hypot(t.pos[i], t.pos[i + 1]) <= 1.3 && t.pos[i + 2] >= 0 && t.pos[i + 2] <= 1);
});
test('a shrub is 30 triangles standing on the ground with outward unit normals', () => {
  const { shrubTemplate } = vm.runInNewContext(`${source('shrubTemplate')}\n({shrubTemplate})`, {});
  const t = shrubTemplate();
  assert.equal(t.vertices, 90);
  let zmin = Infinity, zmax = -Infinity, rmax = 0;
  for (let i = 0; i < t.pos.length; i += 3) { zmin = Math.min(zmin, t.pos[i + 2]); zmax = Math.max(zmax, t.pos[i + 2]); rmax = Math.max(rmax, Math.hypot(t.pos[i], t.pos[i + 1])); }
  assert.equal(zmin, 0); assert.equal(zmax, 1); assert.ok(rmax <= 1.15 && rmax > 0.9);
  for (let tri = 0; tri < 30; tri++) {
    const o = tri * 9, n = [t.nrm[o], t.nrm[o + 1], t.nrm[o + 2]];
    assert.ok(Math.abs(Math.hypot(...n) - 1) < 1e-5);
    const cx = (t.pos[o] + t.pos[o + 3] + t.pos[o + 6]) / 3, cy = (t.pos[o + 1] + t.pos[o + 4] + t.pos[o + 7]) / 3;
    const cz = ((t.pos[o + 2] + t.pos[o + 5] + t.pos[o + 8]) / 3 - 0.45) * 1.4;
    assert.ok(n[0] * cx + n[1] * cy + n[2] * cz > 0, `face ${tri} points outwards`);
  }
});
// ---- packed arrays: what the Python export writes, the viewer's loader reads back exactly
test('arrays packed by the export are unpacked to the same values', async () => {
  const zlib = require('node:zlib');
  const unpackArray = vm.runInNewContext(`${loader.match(/function unpackArray\([^]*?\n\}/)[0]}\nunpackArray`, {
    Float32Array, Uint32Array, Uint8Array });
  const fixture = {"pos": {"z": "H4sIAAAAAAAAA9vAwMDHwMCQz9DMwCDLwOAixLA6wUeCgWF6c7jHAUdvhoMuVs4AJewoTiQAAAA=", "t": "f4", "c": 3, "d": 0}, "idx": {"z": "H4sIAAAAAAAAA2NgYmJgZHxwk5kBDJSUIDQDUBgOAG1GL5UkAAAA", "t": "u4", "c": 1, "d": 1}, "col": {"z": "H4sIAAAAAAAAA+P6zyjCwCTHzgwAY1KlAQkAAAA=", "t": "u1", "c": 3, "d": 0}};
  const read = key => unpackArray(new Uint8Array(zlib.gunzipSync(Buffer.from(fixture[key].z, 'base64'))), fixture[key]);
  assert.deepEqual(Array.from(read('pos')), Array.from(new Float32Array([351261.5, 9968910.25, 1210.125, -3.5, 0, 1e-3, 12.75, -8, 215])));
  assert.deepEqual(Array.from(read('idx')), [0, 1, 2, 2, 1, 0, 70000, 3, 1]);
  assert.deepEqual(Array.from(read('col')), [10, 20, 30, 255, 0, 7, 1, 2, 3]);
  assert.ok(/DecompressionStream\("gzip"\)/.test(loader));
});
