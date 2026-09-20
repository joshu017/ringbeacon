"""Exercise the actual client builders and firmware timing without BLE hardware."""
import ast
import json
import re
from pathlib import Path
import subprocess
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    get_color = get


class ProtocolTests(unittest.TestCase):
    def test_firmware_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = str(Path(tmp) / 'timing')
            # Compile the helpers directly from the sketch, without Arduino/BLE.
            sketch = (ROOT / 'ringbeacon/ringbeacon.ino').read_text()
            helpers = []
            for name in ('cycleDuration', 'animationExpired'):
                match = re.search(r'inline (?:uint32_t|bool) ' + name +
                                  r'\([^)]*\) \{.*?^\}', sketch, re.M | re.S)
                self.assertIsNotNone(match, f'{name} not found in sketch')
                helpers.append(match.group())
            source = Path(tmp) / 'timing.cpp'
            source.write_text('#include <stdint.h>\n' + '\n'.join(helpers) + '\n' +
                              (ROOT / 'tests/timing.cpp').read_text())
            subprocess.run(['c++', '-std=c++11', str(source), '-o', binary], check=True)
            subprocess.run([binary], check=True)

    def python_payload(self, duration=1000, cycles=3, timeout=0):
        # Load the actual method without requiring Tk or Bluetooth at test time.
        tree = ast.parse((ROOT / 'ringbeacon.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'RingGUI')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_build_payload')
        module = ast.Module(body=[method], type_ignores=[])
        namespace = {'COMMAND_PARAMS': {}, 'tk': types.SimpleNamespace(TclError=ValueError)}
        exec(compile(module, 'ringbeacon.py', 'exec'), namespace)
        fake = types.SimpleNamespace(_current_cmd='blink', _param_widgets={})
        for key, value in dict(color_swatch=[0, 255, 255], color2_swatch=[0, 0, 0],
                               duration_var=duration, timeout_var=timeout, cycles_var=cycles,
                               bright_var=255, easing_var='linear', reverse_var=False).items():
            setattr(fake, '_' + key, Value(value))
        return namespace['_build_payload'](fake)

    def web_payload(self, duration=1000, cycles=3, timeout=0):
        html = (ROOT / 'web/public/index.html').read_text()
        script = html.split('<script>')[1].split('</script>')[0]
        # Parse the entire application, but replace the DOM-dependent boot call.
        script = script.rsplit('init();', 1)[0]
        values = {'param-duration': duration, 'param-timeout': timeout,
                  'param-cycles': cycles, 'param-brightness': 255, 'param-easing': 'linear',
                  'cparam-duty': 0.5}
        harness = '''
currentCmd = 'blink';
getColorRgb = prefix => prefix === 'color' ? [0,255,255] : [0,0,0];
const values = VALUES;
globalThis.document = {getElementById: id => id in values ? {value: values[id]} : {checked:false}};
console.log(JSON.stringify(buildPayload()));
'''.replace('VALUES', json.dumps(values))
        return json.loads(subprocess.check_output(['node', '-e', script + harness], text=True))

    def test_reported_three_blinks(self):
        expected = {'cmd': 'blink', 'color': [0, 255, 255], 'duration': 1000, 'cycles': 3}
        self.assertEqual(self.python_payload(), expected)
        self.assertEqual(self.web_payload(), expected)

    def test_total_timeout_and_unlimited_cycles(self):
        expected = {'cmd': 'blink', 'color': [0, 255, 255], 'duration': 250, 'timeout': 2000}
        self.assertEqual(self.python_payload(250, 0, 2000), expected)
        self.assertEqual(self.web_payload(250, 0, 2000), expected)

    def test_zero_duration_is_clamped(self):
        self.assertEqual(self.python_payload(0)['duration'], 1)
        self.assertEqual(self.web_payload(0)['duration'], 1)

    def test_ble_uuids_match(self):
        import re
        sources = [ROOT / 'ringbeacon/ringbeacon.ino', ROOT / 'ringbeacon.py',
                   ROOT / 'web/public/index.html']
        uuids = [set(re.findall(r'c0de1234-beef-cafe-1234-[0-9a-f]{12}', p.read_text())) for p in sources]
        self.assertEqual(len(uuids[0]), 3)
        self.assertEqual(uuids[0], uuids[1])
        self.assertEqual(uuids[0], uuids[2])
