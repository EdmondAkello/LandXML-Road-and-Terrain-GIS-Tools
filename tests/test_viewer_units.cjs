// Exercise the shipped viewer functions without a browser or a JavaScript dependency.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../landxml/viewer.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
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
