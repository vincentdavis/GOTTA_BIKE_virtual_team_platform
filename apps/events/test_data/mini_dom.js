// A small DOM, with no dependencies, for running a rendered page's own inline scripts under
// node. It parses the page into elements and supports the DOM calls and selectors the event
// pages' scripts make; anything else throws, so a script that grows past it fails loudly
// rather than passing on a stub. Not a browser: no layout, no CSS, and inline handler
// attributes (onclick="...") are not wired -- a check calls the page's functions instead.
//
// Used by squad_tag_filter_check.js and squad_tag_editor_check.js.
'use strict';
const vm = require('vm');

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const RAW = new Set(['script', 'style', 'textarea', 'title']);
// An open element of the same kind that the next start tag closes, as an HTML parser would.
const CLOSES = {li: ['li'], option: ['option'], p: ['p'], tr: ['tr', 'td', 'th'], td: ['td', 'th'], th: ['td', 'th']};
const ENTITIES = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0', times: '\u00d7', middot: '\u00b7', hellip: '\u2026', rarr: '\u2192', larr: '\u2190', mdash: '\u2014', ndash: '\u2013'};

function decode(s) {
    return s.replace(/&(#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (m, e) => {
        if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
        return e in ENTITIES ? ENTITIES[e] : m;
    });
}

class Event {
    constructor(type, init = {}) {
        Object.assign(this, init);
        this.type = type;
        this.bubbles = !!init.bubbles;
        this.defaultPrevented = false;
        this.target = null;
        this.currentTarget = null;
        this.propagationStopped = false;
    }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() { this.propagationStopped = true; }
}
class KeyboardEvent extends Event {}

class Node {
    constructor(doc) {
        this.ownerDocument = doc;
        this.parentNode = null;
        this.childNodes = [];
        this.listeners = {};
    }
    get children() { return this.childNodes.filter(n => n.nodeType === 1); }
    get firstElementChild() { return this.children[0] || null; }
    siblingElement(step) {
        if (!this.parentNode) return null;
        const siblings = this.parentNode.children;
        return siblings[siblings.indexOf(this) + step] || null;
    }
    get nextElementSibling() { return this.siblingElement(1); }
    get previousElementSibling() { return this.siblingElement(-1); }
    get textContent() { return this.childNodes.map(n => n.textContent).join(''); }
    set textContent(value) {
        this.childNodes.forEach(n => { n.parentNode = null; });
        this.childNodes = [];
        if (value !== '' && value !== null && value !== undefined) this.appendChild(new Text(this.ownerDocument, String(value)));
    }
    appendChild(node) { return this.insertBefore(node, null); }
    insertBefore(node, ref) {
        if (node.parentNode) node.parentNode.removeChild(node);
        const at = ref ? this.childNodes.indexOf(ref) : -1;
        if (at === -1) this.childNodes.push(node); else this.childNodes.splice(at, 0, node);
        node.parentNode = this;
        return node;
    }
    removeChild(node) {
        const at = this.childNodes.indexOf(node);
        if (at !== -1) this.childNodes.splice(at, 1);
        node.parentNode = null;
        return node;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    contains(node) {
        for (let n = node; n; n = n.parentNode) if (n === this) return true;
        return false;
    }
    descendants() {
        const out = [];
        const walk = node => node.children.forEach(child => { out.push(child); walk(child); });
        walk(this);
        return out;
    }
    querySelectorAll(selector) {
        const groups = parseSelector(selector);
        return this.descendants().filter(el => groups.some(parts => matchesComplex(el, parts, parts.length - 1)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn); }
    dispatchEvent(ev) {
        if (!ev.target) ev.target = this;
        for (let node = this; node; node = ev.bubbles ? node.parentNode : null) {
            ev.currentTarget = node;
            (node.listeners[ev.type] || []).slice().forEach(fn => fn.call(node, ev));
            if (ev.propagationStopped) break;
        }
        return !ev.defaultPrevented;
    }
}

class Text extends Node {
    constructor(doc, data) {
        super(doc);
        this.nodeType = 3;
        this.data = data;
    }
    get textContent() { return this.data; }
    set textContent(value) { this.data = String(value); }
}

class Element extends Node {
    constructor(doc, tagName) {
        super(doc);
        this.nodeType = 1;
        this.localName = tagName.toLowerCase();
        this.tagName = this.localName.toUpperCase();
        this.attrs = new Map();
        this.style = {};
        this._value = null;
        this._checked = null;
    }
    getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
    hasAttribute(name) { return this.attrs.has(name); }
    setAttribute(name, value) {
        this.attrs.set(name, String(value));
        if (name === 'style') this.readStyle();
    }
    removeAttribute(name) { this.attrs.delete(name); }
    readStyle() {
        this.style = {};
        for (const decl of (this.getAttribute('style') || '').split(';')) {
            const at = decl.indexOf(':');
            if (at === -1) continue;
            const prop = decl.slice(0, at).trim().replace(/-(\w)/g, (_, c) => c.toUpperCase());
            this.style[prop] = decl.slice(at + 1).trim();
        }
    }
    get id() { return this.getAttribute('id') || ''; }
    set id(value) { this.setAttribute('id', value); }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', value); }
    get classList() {
        const el = this;
        const list = () => el.className.split(/\s+/).filter(Boolean);
        const write = classes => el.setAttribute('class', classes.join(' '));
        return {
            contains: c => list().includes(c),
            add: (...cs) => write([...new Set([...list(), ...cs])]),
            remove: (...cs) => write(list().filter(c => !cs.includes(c))),
            toggle: (c, force) => {
                const on = force === undefined ? !list().includes(c) : !!force;
                if (on) write([...new Set([...list(), c])]); else write(list().filter(x => x !== c));
                return on;
            },
        };
    }
    get hidden() { return this.hasAttribute('hidden'); }
    set hidden(value) { if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
    get disabled() { return this.hasAttribute('disabled'); }
    set disabled(value) { if (value) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
    get type() { return (this.getAttribute('type') || (this.localName === 'button' ? 'submit' : '')).toLowerCase(); }
    set type(value) { this.setAttribute('type', value); }
    get value() {
        if (this._value !== null) return this._value;
        return this.localName === 'textarea' ? this.textContent : (this.getAttribute('value') || '');
    }
    set value(value) { this._value = String(value); }
    get checked() { return this._checked === null ? this.hasAttribute('checked') : this._checked; }
    set checked(value) { this._checked = !!value; }
    get form() { return this.closest('form'); }
    get cells() { return this.children.filter(c => c.localName === 'td' || c.localName === 'th'); }
    get innerHTML() { throw new Error('mini_dom: reading innerHTML is not supported'); }
    set innerHTML(html) {
        this.textContent = '';
        parseInto(this, String(html));
    }
    matches(selector) { return parseSelector(selector).some(parts => matchesComplex(this, parts, parts.length - 1)); }
    closest(selector) {
        for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (n.matches(selector)) return n;
        return null;
    }
    focus() { this.ownerDocument.activeElement = this; }
    blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
    scrollIntoView() {}
    // A click, and for a submit button in a form, the submit that follows it. The form
    // records whether that submit went ahead (form.submitted) -- nothing is sent anywhere.
    click() {
        if (this.disabled) return;
        const ev = new Event('click', {bubbles: true});
        if (!this.dispatchEvent(ev)) return;
        const form = this.form;
        if (form && (this.localName === 'button' || this.localName === 'input') && this.type === 'submit') {
            form.submitted = form.dispatchEvent(new Event('submit', {bubbles: true}));
        }
    }
}

class Document extends Node {
    constructor(html) {
        super(null);
        this.ownerDocument = this;
        this.nodeType = 9;
        parseInto(this, html);
        this.body = this.querySelector('body') || this;
        this.activeElement = this.body;
    }
    createElement(tagName) { return new Element(this, tagName); }
    createTextNode(data) { return new Text(this, String(data)); }
    getElementById(id) { return this.descendants().find(el => el.getAttribute('id') === id) || null; }
}

function parseInto(parent, html) {
    const doc = parent.ownerDocument;
    const lower = html.toLowerCase();
    const stack = [parent];
    const top = () => stack[stack.length - 1];
    const addText = raw => { if (raw) top().appendChild(new Text(doc, decode(raw))); };
    const tagRe = /<!--[\s\S]*?-->|<![^>]*>|<\/([a-zA-Z][\w:-]*)\s*>|<([a-zA-Z][\w:-]*)((?:\s+[^\s"'>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/g;
    const attrRe = /([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let last = 0;
    let m;
    while ((m = tagRe.exec(html))) {
        addText(html.slice(last, m.index));
        last = tagRe.lastIndex;
        if (m[0].startsWith('<!')) continue;
        if (m[1]) {
            const name = m[1].toLowerCase();
            for (let i = stack.length - 1; i > 0; i--) {
                if (stack[i].localName === name) { stack.length = i; break; }
            }
            continue;
        }
        const name = m[2].toLowerCase();
        while (CLOSES[name] && stack.length > 1 && CLOSES[name].includes(top().localName)) stack.pop();
        const el = new Element(doc, name);
        for (const a of m[3].matchAll(attrRe)) {
            const attr = a[1].toLowerCase();
            if (!el.attrs.has(attr)) el.attrs.set(attr, decode(a[2] ?? a[3] ?? a[4] ?? ''));
        }
        el.readStyle();
        top().appendChild(el);
        if (RAW.has(name)) {
            const end = lower.indexOf('</' + name, last);
            const stop = end === -1 ? html.length : end;
            const raw = html.slice(last, stop);
            if (raw) el.appendChild(new Text(doc, name === 'textarea' || name === 'title' ? decode(raw) : raw));
            last = end === -1 ? html.length : html.indexOf('>', end) + 1;
            tagRe.lastIndex = last;
            continue;
        }
        if (!VOID.has(name) && !m[4]) stack.push(el);
    }
    addText(html.slice(last));
}

// Selectors: tag, *, #id, .class, [attr], [attr=v], [attr^=v], [attr$=v], [attr*=v], [attr~=v],
// combined, joined by descendant (space) or child (>) combinators, in comma-separated lists.
function parseSelector(selector) {
    return splitTopLevel(selector, ',').map(complex => {
        const parts = [];
        const re = /\s*(>)\s*|\s+|((?:[a-zA-Z*][\w-]*)?(?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)/g;
        let m;
        let pos = 0;
        const src = complex.trim();
        while (pos < src.length) {
            re.lastIndex = pos;
            m = re.exec(src);
            if (!m || m.index !== pos || m[0] === '') throw new Error(`mini_dom: unsupported selector ${JSON.stringify(selector)}`);
            pos = re.lastIndex;
            const afterCombinator = typeof parts[parts.length - 1] === 'string';
            if (m[1]) {
                // A child combinator, possibly with spaces around it.
                if (!parts.length) throw new Error(`mini_dom: unsupported selector ${JSON.stringify(selector)}`);
                if (afterCombinator) parts[parts.length - 1] = '>'; else parts.push('>');
            } else if (m[2] === undefined) {
                // Whitespace: a descendant combinator unless a '>' follows.
                if (parts.length && !afterCombinator) parts.push(' ');
            } else {
                parts.push(parseCompound(m[2], selector));
            }
        }
        return parts;
    });
}

function splitTopLevel(s, sep) {
    const out = [];
    let depth = 0;
    let quote = null;
    let start = 0;
    for (let i = 0; i < s.length; i++) {
        const c = s[i];
        if (quote) { if (c === quote) quote = null; continue; }
        if (c === '"' || c === "'") quote = c;
        else if (c === '[' || c === '(') depth++;
        else if (c === ']' || c === ')') depth--;
        else if (c === sep && depth === 0) { out.push(s.slice(start, i)); start = i + 1; }
    }
    out.push(s.slice(start));
    return out;
}

function parseCompound(text, selector) {
    const compound = {tag: null, id: null, classes: [], attrs: []};
    const re = /^([a-zA-Z][\w-]*|\*)|#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]/g;
    let m;
    let pos = 0;
    while (pos < text.length) {
        re.lastIndex = pos;
        m = re.exec(text);
        if (!m || m.index !== pos) throw new Error(`mini_dom: unsupported selector ${JSON.stringify(selector)}`);
        pos = re.lastIndex;
        if (m[1]) compound.tag = m[1] === '*' ? null : m[1].toLowerCase();
        else if (m[2]) compound.id = m[2];
        else if (m[3]) compound.classes.push(m[3]);
        else compound.attrs.push({name: m[4].toLowerCase(), op: m[5] || null, value: m[6] ?? m[7] ?? m[8] ?? null});
    }
    return compound;
}

function matchCompound(el, c) {
    if (c.tag && el.localName !== c.tag) return false;
    if (c.id && el.getAttribute('id') !== c.id) return false;
    const classes = el.className.split(/\s+/);
    if (!c.classes.every(cls => classes.includes(cls))) return false;
    return c.attrs.every(({name, op, value}) => {
        const got = el.getAttribute(name);
        if (got === null) return false;
        switch (op) {
            case null: return true;
            case '=': return got === value;
            case '^=': return value !== '' && got.startsWith(value);
            case '$=': return value !== '' && got.endsWith(value);
            case '*=': return value !== '' && got.includes(value);
            case '~=': return got.split(/\s+/).includes(value);
            default: throw new Error(`mini_dom: unsupported attribute operator ${op}`);
        }
    });
}

function matchesComplex(el, parts, i) {
    if (!el || el.nodeType !== 1 || !matchCompound(el, parts[i])) return false;
    if (i === 0) return true;
    const combinator = parts[i - 1];
    if (combinator === '>') return matchesComplex(el.parentNode, parts, i - 2);
    for (let p = el.parentNode; p && p.nodeType === 1; p = p.parentNode) {
        if (matchesComplex(p, parts, i - 2)) return true;
    }
    return false;
}

// Loads a rendered page, exposes it as this process's document (with localStorage holding
// `storage`), and returns a runner for the page's inline scripts: run(marker) runs the first
// <script> whose text contains the marker, at global scope, so its function declarations
// become globals as in a browser.
function loadPage(html, storage = {}) {
    const document = new Document(html);
    const store = {...storage};
    Object.assign(globalThis, {
        document,
        window: globalThis,
        Event,
        KeyboardEvent,
        localStorage: {
            getItem: k => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
            removeItem: k => { delete store[k]; },
        },
    });
    const scripts = document.querySelectorAll('script').filter(s => !s.hasAttribute('src') && !s.hasAttribute('type'));
    return {
        document,
        run(marker) {
            const script = scripts.find(s => s.textContent.includes(marker));
            if (!script) throw new Error(`mini_dom: no inline script contains ${JSON.stringify(marker)}`);
            vm.runInThisContext(script.textContent);
        },
    };
}

// Collects named checks and prints one line each; exits 1 if any failed.
function checker() {
    const results = [];
    return {
        check(label, got, want) {
            const g = JSON.stringify(got);
            const w = JSON.stringify(want);
            results.push({ok: g === w, label, got: g, want: w});
        },
        finish() {
            for (const r of results) console.log(`${r.ok ? 'ok ' : 'BAD'} | ${r.label} | got ${r.got}${r.ok ? '' : ` | want ${r.want}`}`);
            process.exit(results.length && results.every(r => r.ok) ? 0 : 1);
        },
    };
}

module.exports = {loadPage, checker, Event, KeyboardEvent};
