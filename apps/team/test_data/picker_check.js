// Runs the verification form's own inline script against a stub DOM and checks what the
// file picker offers and what it says about a chosen file, before anything is uploaded.
//
//   node picker_check.js page.html
//
// Used by apps/team/test_evidence_photos.py, which renders /user/verification/ and passes the
// page in. Expected messages are read back from the page's data-* attributes, so this checks
// the SCRIPT; the Python tests check that those attributes carry the validator's wording.
// Prints one line per check and exits 1 if any failed.
'use strict';
const fs = require('fs');

const html = fs.readFileSync(process.argv[2], 'utf8');
const decode = s => s
    .replace(/&#x27;/g, "'").replace(/&#39;/g, "'").replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map(m => m[1]).find(s => s.includes('fileProblem'));
const inputTag = html.match(/<input[^>]*id="id_media_file"[^>]*>/)[0];
const attrs = {};
for (const m of inputTag.matchAll(/([\w-]+)="([^"]*)"/g)) attrs[m[1]] = decode(m[2]);
const mediaTypesJson = html.match(
    /<script id="media-types-by-verify-type" type="application\/json">([\s\S]*?)<\/script>/)[1];
const selectHtml = html.match(/<select[^>]*id="id_media_type"[\s\S]*?<\/select>/)[0];
const options = [...selectHtml.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)]
    .map(m => ({value: m[1], text: m[2]}));

function element(id) {
    const listeners = {};
    const attributes = {};
    const classes = new Set();
    const el = {
        id, dataset: {}, style: {}, textContent: '', value: '', disabled: false, required: false,
        children: [], options: [],
        classList: {
            add: c => classes.add(c),
            remove: c => classes.delete(c),
            contains: c => classes.has(c),
            toggle: (c, force) => ((force === undefined ? !classes.has(c) : force) ? classes.add(c) : classes.delete(c)),
        },
        getAttribute: k => (k in attributes ? attributes[k] : null),
        setAttribute: (k, v) => { attributes[k] = String(v); },
        addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
        dispatchEvent: ev => { (listeners[ev.type] || []).forEach(fn => fn.call(el, ev)); return true; },
        querySelectorAll: () => [],
        querySelector: () => null,
        appendChild: child => { el.children.push(child); },
        scrollIntoView: () => {},
        focus: () => {},
    };
    Object.defineProperty(el, 'innerHTML', {get: () => '', set: () => { el.children = []; }});
    return el;
}

const elements = {};
const byId = id => (elements[id] ||= element(id));
const fileInput = byId('id_media_file');
for (const [key, value] of Object.entries(attrs)) {
    fileInput.setAttribute(key, value);
    if (key.startsWith('data-')) {
        fileInput.dataset[key.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = value;
    }
}
// The script disables the inputs inside this wrapper when "Other" is chosen.
byId('media-file-field').querySelectorAll = selector => (selector === 'input' ? [fileInput] : []);
byId('media-types-by-verify-type').textContent = mediaTypesJson;
const mediaType = byId('id_media_type');
mediaType.options = options;
mediaType.value = options[0].value;

global.Event = class { constructor(type) { this.type = type; } };
global.document = {
    getElementById: byId,
    querySelectorAll: () => [],
    querySelector: () => null,
    createElement: () => element('created'),
};
new Function(script)();

const MB = 1024 * 1024;
const maxMb = parseInt(attrs['data-max-mb'], 10);
const allowed = attrs.accept.split(',');
const results = [];
const check = (label, got, want) => results.push({ok: got === want, label, got, want});

function choose(type, name, size) {
    mediaType.value = type;
    mediaType.dispatchEvent(new Event('change'));
    fileInput.files = [{name, size}];
    fileInput.dispatchEvent(new Event('change'));
    const banner = byId('file-error');
    const shown = !banner.classList.contains('hidden') && banner.children.length;
    return {
        message: shown ? banner.children[banner.children.length - 1].textContent : '',
        submitDisabled: byId('submit-btn').disabled,
    };
}

for (const [type, want] of [['photo', attrs['data-accept-photo']], ['video', attrs['data-accept-video']], ['link', attrs.accept]]) {
    mediaType.value = type;
    mediaType.dispatchEvent(new Event('change'));
    check(`accept for ${type}`, fileInput.getAttribute('accept'), want);
}
check('photo + IMG_0005.DNG', choose('photo', 'IMG_0005.DNG', 30 * MB).message, attrs['data-raw-message']);
check('photo + clip.mp4', choose('photo', 'clip.mp4', MB).message, attrs['data-wrong-kind-photo']);
check('video + IMG_0001.HEIC', choose('video', 'IMG_0001.HEIC', MB).message, attrs['data-wrong-kind-video']);
check('video + a clip over the limit', choose('video', 'clip.mov', (maxMb + 50) * MB).message,
    `clip.mov is ${maxMb + 50} MB. The limit is ${maxMb} MB — please trim or compress it, then choose the file again.`);
check('photo + doc.pdf', choose('photo', 'doc.pdf', MB).message,
    `doc.pdf is not an allowed file type. Allowed: ${allowed.join(', ')}`);
check('photo + ..heic (no extension, as the server reads it)', choose('photo', '..heic', MB).message,
    `..heic is not an allowed file type. Allowed: ${allowed.join(', ')}`);
check('photo + IMG_0001.HEIC is fine', choose('photo', 'IMG_0001.HEIC', 3 * MB).message, '');
check('link + clip.mp4 is fine', choose('link', 'clip.mp4', MB).message, '');
check('video + clip.MP4 leaves Submit enabled', choose('video', 'clip.MP4', MB).submitDisabled, false);
check('photo + clip.mp4 disables Submit', choose('photo', 'clip.mp4', MB).submitDisabled, true);
choose('photo', 'clip.mp4', MB);
mediaType.value = 'other';
mediaType.dispatchEvent(new Event('change'));
check('switching to Other clears the refusal', byId('submit-btn').disabled, false);

for (const r of results) {
    console.log(`${r.ok ? 'ok ' : 'BAD'} | ${r.label} | got ${JSON.stringify(r.got)}${r.ok ? '' : ` | want ${JSON.stringify(r.want)}`}`);
}
process.exit(results.every(r => r.ok) ? 0 : 1);
