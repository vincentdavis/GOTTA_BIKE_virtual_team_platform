// Runs the event edit page's own squad-tag chip editor against mini_dom.js and checks what
// it writes to the hidden squad_tags input, what it says, and what happens on Save.
//
//   node squad_tag_editor_check.js page.html
//
// Used by apps/events/test_squad_tags.py, which renders the edit page of an event offering
// the tags Red and Blue. Prints one line per check and exits 1 if any failed.
'use strict';
const fs = require('fs');
const {loadPage, checker, KeyboardEvent} = require('./mini_dom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const {check, finish} = checker();
const page = loadPage(html);
page.run('Squad tags chip editor');

const doc = page.document;
const hidden = doc.getElementById('id_squad_tags');
const input = doc.getElementById('squad-tag-input');
const chips = doc.getElementById('squad-tag-chips');
const status = () => doc.getElementById('squad-tag-status').textContent;
const saved = () => JSON.parse(hidden.value);
const maxTags = parseInt(hidden.getAttribute('data-max-tags'), 10);
const maxLength = parseInt(hidden.getAttribute('data-max-length'), 10);
const saveButton = hidden.form.querySelector('button[type="submit"]');

function pressEnter(isComposing = false) {
    const ev = new KeyboardEvent('keydown', {key: 'Enter', isComposing, bubbles: true});
    input.dispatchEvent(ev);
    return ev.defaultPrevented;
}
function type(text) {
    input.value = text;
    return pressEnter();
}
function save() {
    hidden.form.submitted = undefined;
    saveButton.click();
    return hidden.form.submitted;
}

check('the saved tags load as chips', chips.querySelectorAll('li').map(li => li.querySelector('span').textContent), ['Red', 'Blue']);
check('each remove button names its tag', chips.querySelectorAll('button').map(b => b.getAttribute('aria-label')),
    ['Remove tag Red', 'Remove tag Blue']);
check('the remove glyph is hidden from screen readers',
    chips.querySelectorAll('button span').map(s => s.getAttribute('aria-hidden')), ['true', 'true']);

check('Enter in the tag box does not submit the form', type('  Tall   Squad '), true);
check('Enter adds the tidied tag, keeping its case', saved(), ['Red', 'Blue', 'Tall Squad']);
check('the box is emptied', input.value, '');
check('the addition is announced', status(), 'Added tag Tall Squad.');
check('the new chip names its tag too', chips.querySelectorAll('button').map(b => b.getAttribute('aria-label'))[2], 'Remove tag Tall Squad');

input.value = 'Green';
check('Enter while an input method is composing is left alone', pressEnter(true), false);
check('and adds nothing', saved(), ['Red', 'Blue', 'Tall Squad']);

check('a case-duplicate is not added', (type('rED'), saved()), ['Red', 'Blue', 'Tall Squad']);
check('and says which tag it matches', status(), 'Red is already in the list.');

doc.getElementById('squad-tag-add-btn').click();
check('Add with an empty box adds nothing', saved(), ['Red', 'Blue', 'Tall Squad']);
input.value = 'Short';
doc.getElementById('squad-tag-add-btn').click();
check('the Add button adds the tag', saved(), ['Red', 'Blue', 'Tall Squad', 'Short']);

chips.querySelectorAll('button')[1].click();
check('a remove button removes its tag', saved(), ['Red', 'Tall Squad', 'Short']);
check('and says so', status(), 'Removed tag Blue.');
check('focus moves to the chip that took its place', doc.activeElement.getAttribute('aria-label'), 'Remove tag Tall Squad');

// Save pressed with a tag still in the box.
input.value = ' Purple ';
check('Save with a tag still typed goes ahead', save(), true);
check('and saves that tag with the rest', saved(), ['Red', 'Tall Squad', 'Short', 'Purple']);

input.value = '   ';
check('Save with only spaces in the box goes ahead', save(), true);
check('and adds nothing', saved().length, 4);

input.value = 'x'.repeat(maxLength + 1);
check('Save with a typed tag that is too long is stopped', save(), false);
check('and says why', status(),
    `The event was not saved. A squad tag can be at most ${maxLength} characters. Or clear the Squad tags box to save without it.`);
check('and puts focus back in the box', doc.activeElement === input, true);
check('the list is unchanged', saved().length, 4);

input.value = '';
for (let i = saved().length; i < maxTags; i++) type(`Tag ${i}`);
check('the list fills to the limit', saved().length, maxTags);
input.value = 'One more';
check('Save with a typed tag over the limit is stopped', save(), false);
check('and says why', status(),
    `The event was not saved. An event can have at most ${maxTags} squad tags. Remove one first. Or clear the Squad tags box to save without it.`);
check('the typed tag stays in the box', input.value, 'One more');
input.value = 'short';
check('Save with a typed duplicate goes ahead even when full', save(), true);
check('and leaves the list as it was', saved().length, maxTags);

finish();
