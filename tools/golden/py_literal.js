// JS-значение → Python-литерал для сгенерированных golden-модулей.
'use strict';

function py(value) {
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'number' || typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(py).join(', ') + ']';
  return '{' + Object.keys(value).map((k) => `${JSON.stringify(k)}: ${py(value[k])}`).join(', ') + '}';
}

module.exports = { py };
