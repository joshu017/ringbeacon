import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const app = script.slice(0, script.lastIndexOf('init();'));

function payload(duration = 1000, cycles = 3, timeout = 0, cmd = 'blink') {
  const values = {
    'param-duration': duration, 'param-cycles': cycles, 'param-timeout': timeout,
    'param-brightness': 255, 'param-easing': 'linear', 'cparam-duty': 0.5,
  };
  const context = vm.createContext({
    document: { getElementById: id => id in values ? { value: values[id] } : { checked: false } },
    command: cmd,
  });
  const result = vm.runInContext(app + `
    currentCmd = command;
    getColorRgb = prefix => prefix === 'color' ? [0,255,255] : [0,0,0];
    JSON.stringify(buildPayload());
  `, context);
  return JSON.parse(result);
}

test('three cyan blinks use per-cycle duration', () => {
  assert.deepEqual(payload(), { cmd: 'blink', color: [0,255,255], duration: 1000, cycles: 3 });
});
test('total timeout is separate and unlimited cycles are omitted', () => {
  assert.deepEqual(payload(250, 0, 2000), { cmd: 'blink', color: [0,255,255], duration: 250, timeout: 2000 });
});
test('zero duration is clamped to one millisecond', () => {
  assert.equal(payload(0).duration, 1);
});
test('off has no animation parameters', () => {
  assert.deepEqual(payload(1000, 3, 5000, 'off'), { cmd: 'off' });
});
