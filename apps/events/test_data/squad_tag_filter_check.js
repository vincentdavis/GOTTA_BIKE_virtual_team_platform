// Runs the event page's own squads-table script (details rows, Expand all, column toggler,
// sort and the tag filter) against mini_dom.js and checks what the tag filter shows.
//
//   node squad_tag_filter_check.js page.html
//
// Used by apps/events/test_squad_tags.py, which renders an event offering the tags Red, Blue
// and Tall, with the squads Alpha [Red], Bravo [Blue], Charlie [] and Delta [Red, Blue].
// Prints one line per check and exits 1 if any failed.
'use strict';
const fs = require('fs');
const {loadPage, checker} = require('./mini_dom');

const html = fs.readFileSync(process.argv[2], 'utf8');
const MARKER = 'function toggleAllSquadMembers';
const {check, finish} = checker();

let page = loadPage(html);
// Delta carries its tags in another case than the buttons, to show matching ignores case.
// (The server writes the event's spelling, so only a hand-edited page could differ.)
const deltaRow = page.document.querySelectorAll('#squad-table tbody tr.squad-row')
    .find(row => row.cells[1].textContent.trim() === 'Delta');
deltaRow.setAttribute('data-squad-tags', '["rED", "bLUE"]');
page.run(MARKER);

const doc = page.document;
const table = doc.getElementById('squad-table');
const tbody = table.querySelector('tbody');
const pkOf = row => row.querySelector('svg[id^="squad-arrow-"]').id.replace('squad-arrow-', '');
const nameOf = row => row.cells[1].textContent.trim();
const squadRows = () => tbody.querySelectorAll('tr.squad-row');
const rowNamed = name => squadRows().find(row => nameOf(row) === name);
const detailOf = name => doc.getElementById('squad-members-' + pkOf(rowNamed(name)));
const shown = () => squadRows().filter(row => !row.hidden).map(nameOf);
const status = () => doc.getElementById('squad-tag-filter-status').textContent;
const button = label => doc.querySelectorAll('#squad-tag-filter button').find(b => b.textContent.trim() === label);
const pressed = () => doc.querySelectorAll('#squad-tag-filter button')
    .filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.textContent.trim());
const header = label => table.querySelectorAll('th[data-sort]').find(th => th.textContent.trim().startsWith(label));
// Every squad row is directly followed by its own details row.
const pairsIntact = () => squadRows().every(row => {
    const next = row.nextElementSibling;
    return !!next && next.id === 'squad-members-' + pkOf(row) && next.hidden === row.hidden;
});

// Unfiltered on load.
check('on load every squad shows', shown(), ['Alpha', 'Bravo', 'Charlie', 'Delta']);
check('on load the status is empty', status(), '');
check('on load only All is pressed', pressed(), ['All']);
check('the Tags column shows by default', header('Tags').style.display || '', '');
const visibleHeaders = table.querySelectorAll('thead th').filter(th => th.style.display !== 'none').length;
check('the details row spans every visible column, Tags included',
    detailOf('Alpha').querySelector('td').getAttribute('colspan'), String(visibleHeaders));

// Bravo's details are open when the filter hides Bravo.
toggleSquadMembers(pkOf(rowNamed('Bravo')));
check('Bravo details open before filtering', detailOf('Bravo').style.display, '');

button('Red').click();
check('Red shows only the squads carrying it, whatever the case', shown(), ['Alpha', 'Delta']);
check('an untagged squad is hidden by a tag', rowNamed('Charlie').hidden, true);
check('a hidden squad hides its open details row', detailOf('Bravo').hidden, true);
check('a hidden squad hides its closed details row', detailOf('Charlie').hidden, true);
check('the status counts what is shown', status(), 'Showing 2 of 4 squads');
check('Red is pressed, All is not', pressed(), ['Red']);
check('the pressed tag looks pressed', [button('Red').classList.contains('btn-neutral'), button('Red').classList.contains('btn-outline')], [true, false]);
check('All no longer looks pressed', [button('All').classList.contains('btn-neutral'), button('All').classList.contains('btn-outline')], [false, true]);

// Expand all acts only on what the filter shows: a filtered-out squad's details row is left
// exactly as it was, open (Bravo) or closed (Charlie), and stays hidden.
toggleAllSquadMembers();
check('Expand all opens the shown squads',
    [detailOf('Alpha').style.display, detailOf('Delta').style.display], ['', '']);
check('Expand all leaves a filtered-out open squad as it was', [detailOf('Bravo').style.display, detailOf('Bravo').hidden], ['', true]);
check('Expand all leaves a filtered-out closed squad as it was', [detailOf('Charlie').style.display, detailOf('Charlie').hidden], ['none', true]);
toggleAllSquadMembers();
check('pressed again, it collapses the shown squads',
    [detailOf('Alpha').style.display, detailOf('Delta').style.display], ['none', 'none']);
check('pressed again, it still leaves them alone',
    [detailOf('Bravo').style.display, detailOf('Charlie').style.display], ['', 'none']);
// Half open: it opens the rest rather than throwing away what was already open.
toggleSquadMembers(pkOf(rowNamed('Alpha')));
toggleAllSquadMembers();
check('a half-expanded table opens the rest and keeps what was open',
    [detailOf('Alpha').style.display, detailOf('Delta').style.display], ['', '']);
// The button's label counts only the squads shown: with both shown squads open it offers to
// collapse, although Charlie's (hidden) details are closed.
for (const name of ['Alpha', 'Delta']) {
    if (detailOf(name).style.display === 'none') toggleSquadMembers(pkOf(rowNamed(name)));
}
check('the Expand all label counts only shown squads', doc.getElementById('squad-expand-all').textContent, 'Collapse all');
for (const name of ['Alpha', 'Delta']) toggleSquadMembers(pkOf(rowNamed(name)));
check('and offers to expand once one of them is closed', doc.getElementById('squad-expand-all').textContent, 'Expand all');

// Sorting keeps each details row with its squad, filtered or not.
header('Tags').click();
check('sorting by Tags keeps details rows paired', pairsIntact(), true);
header('Name').click();
header('Name').click();
check('sorting by Name, descending, keeps details rows paired', pairsIntact(), true);
check('sorting keeps the filter', shown().sort(), ['Alpha', 'Delta']);

// The active tag again clears the filter, as All does.
button('Red').click();
check('clicking the active tag again shows every squad', shown().sort(), ['Alpha', 'Bravo', 'Charlie', 'Delta']);
check('and unhides every details row', squadRows().every(row => !row.nextElementSibling.hidden), true);
check('and empties the status', status(), '');
check('and presses All', pressed(), ['All']);

button('Blue').click();
check('Blue shows only the squads carrying it', shown().sort(), ['Bravo', 'Delta']);
button('All').click();
check('All shows every squad', shown().sort(), ['Alpha', 'Bravo', 'Charlie', 'Delta']);
check('All empties the status', status(), '');

button('Tall').click();
check('a tag no squad carries shows none', shown(), []);
check('and says so', status(), 'Showing 0 of 4 squads');
check('the Expand all label ignores hidden squads', doc.getElementById('squad-expand-all').textContent, 'Expand all');

// A saved column choice still wins over the default.
page = loadPage(html, {event_squad_field_cols: JSON.stringify({sqf_tags: false})});
page.run(MARKER);
const savedTable = page.document.getElementById('squad-table');
check('a saved choice hides the Tags column',
    savedTable.querySelectorAll('th[data-scol="sqf_tags"]').map(th => th.style.display), ['none']);
check('and unticks its menu entry',
    page.document.querySelector('.squad-field-toggle[data-scol="sqf_tags"]').checked, false);

finish();
