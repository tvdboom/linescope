// Keep the handler on the table so independently inserted notebook outputs work.
const button = event.target.closest('.table-sort');
if (!button || button.closest('table') !== this) return;
const header = button.closest('th');
const current = header.getAttribute('aria-sort');
const direction = current ? (current === 'ascending' ? 'descending' : 'ascending') : button.dataset.sortDirection;
const multiplier = direction === 'ascending' ? 1 : -1;
const key = button.dataset.sort;
const body = this.tBodies[0];
const rows = Array.from(body.rows);
rows.sort((left, right) => {
  const tie = Number(left.dataset.order) - Number(right.dataset.order);
  // Original order also retains current-cell and called-source boundaries.
  if (key === 'line') return tie * multiplier;
  const leftValue = left.getAttribute('data-' + key) ?? '';
  const rightValue = right.getAttribute('data-' + key) ?? '';
  // Unknown measurements stay last beside zeroes and negative memory changes.
  if (leftValue === '' || rightValue === '') {
    return (leftValue === '') - (rightValue === '') || tie;
  }
  const comparison = button.dataset.sortType === 'number' ? Number(leftValue) - Number(rightValue) : leftValue.localeCompare(rightValue, undefined, { sensitivity: 'base' });
  return comparison * multiplier || tie;
});
rows.forEach(row => {
  // Source-gap dividers describe adjacency only in the original source order.
  row.classList.toggle('ls-cell-gap', key === 'line' && direction === 'ascending' && Number(row.dataset.gapBefore) > 0);
  body.appendChild(row);
});
this.querySelectorAll('.table-sort').forEach(control => {
  const column = control.closest('th');
  if (control === button) column.setAttribute('aria-sort', direction);
  else column.removeAttribute('aria-sort');
  const nextDirection = control === button ? (direction === 'ascending' ? 'descending' : 'ascending') : control.dataset.sortDirection;
  control.setAttribute('aria-label', 'Sort by ' + control.dataset.sortLabel + ', ' + nextDirection);
});
