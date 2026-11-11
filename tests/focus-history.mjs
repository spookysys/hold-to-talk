import assert from 'node:assert/strict';
import {FocusHistory} from '../gnome/history.js';
const history = new FocusHistory();
history.record(10, 'a');
history.record(30, 'b');
assert.equal(history.capture(20), 'a'); // Worker runs after focus moved.
assert.equal(history.capture(30), 'b');
assert.equal(history.capture(9), ''); // Do not invent a destination before startup.
history.record(40, ''); // No focus, or locked session.
assert.equal(history.capture(41), '');
for (let i = 50; i < 400; i++)
    history.record(i, 'b');
assert.equal(history.entries.length, 256);
assert.equal(history.capture(20), ''); // Expired history fails closed.
console.log('Focus history checks passed.');
