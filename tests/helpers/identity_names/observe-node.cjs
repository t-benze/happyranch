// Naming-only passive native child observation. Returns the original handles;
// changes no argv, environment, spawn result, callback, or product behavior.
const cp = require('node:child_process');
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
const destination = process.env.NAMING_NODE_CHILDREN;
if (!destination) throw new Error('naming child observation destination missing');
function write(row) {
  const raw = JSON.stringify(row) + '\n';
  const size = fs.existsSync(destination) ? fs.statSync(destination).size : 0;
  if (Buffer.byteLength(raw) > 16384 || size + Buffer.byteLength(raw) > 1048576)
    throw new Error('naming native child observation cap exceeded');
  fs.appendFileSync(destination, raw, { mode: 0o600 });
}
const original = cp.spawn;
cp.spawn = function (...input) {
  const child = original.apply(this, input);
  let start = null;
  if (child.pid) {
    try {
      const stat = fs.readFileSync(`/proc/${child.pid}/stat`, 'utf8');
      start = stat.slice(stat.lastIndexOf(') ') + 2).split(' ')[19];
    } catch { /* The exact returned handle's exit remains observable. */ }
  }
  write({ kind: 'spawn', parent: process.pid, pid: child.pid ?? null,
          start, executable: input[0], argv: Array.isArray(input[1]) ? input[1] : [] });
  child.once('exit', (code, signal) => write({ kind: 'exit', pid: child.pid, code, signal }));
  return child;
};
syncBuiltinESMExports();
